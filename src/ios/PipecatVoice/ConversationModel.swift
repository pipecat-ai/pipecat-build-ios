import AVFoundation
import Foundation
import Observation

struct SpeechActivityEvent: Identifiable {
    let id = UUID()
    let role: String
    let speaking: Bool
    let timestamp = Date()
}

private struct NativeEvent: Decodable {
    let type: String
    var operation: String?
    var provider: String?
    var request: String?
    var turn: String?
    var session: String?
    var state: String?
    var text: String?
    var prompt: String?
    var instructions: String?
    var history: [HistoryMessage]?
    var message: String?
}

private struct RTVIEnvelope: Decodable {
    let type: String
    let turn: String?
    let session: String?
    let message: Message
    struct Message: Decodable {
        let type: String
        let data: Payload?
    }
    struct Payload: Decodable {
        let text: String?
        let final: Bool?
    }
}

enum VoicePhase: String {
    case idle, preparing, listening, thinking, speaking, muted
    var title: String {
        switch self {
        case .idle: "Ready when you are"
        case .preparing: "Getting your voice ready"
        case .listening: "I’m listening"
        case .thinking: "Thinking it through"
        case .speaking: "Pipecat is speaking"
        case .muted: "Microphone is muted"
        }
    }
}

@MainActor @Observable
final class ConversationModel {
    var messages: [TranscriptMessage] = []
    var partial = ""
    var phase: VoicePhase = .idle
    var userLevel = 0.0
    var botLevel = 0.0
    var botAudioPlaying = false
    var active = false
    var muted = false
    var ready = false
    var error: String?
    var showSettings = false
    private var hasKey = false
    var userSpeaking = false
    var botSpeaking = false
    var speechEvents: [SpeechActivityEvent] = []
    private(set) var voiceConfiguration = VoiceSettings.load()
    var selectedProvider: TTSProviderID { voiceConfiguration.effectiveProvider }
    var selectedVoice: String { voiceConfiguration.voice(for: selectedProvider) }
    var needsVoiceSetup: Bool { selectedProvider == .phonon && !hasKey }
    var availability: String? = AppleLanguageModel.unavailableReason
    var transcriptMessages: [TranscriptMessage] {
        TranscriptMessage.displaying(messages, partial: partial, turn: currentTurn)
    }

    private let runtime = PCPythonRuntime()
    private let audio: VoiceAudioEngine
    private let speech: AppleSpeechRecognizer
    private var synthesizer: (any SpeechSynthesizer)?
    private let synthesizerFactory: (TTSProviderID) throws -> any SpeechSynthesizer
    private let player: PCMPlayer
    private var requests: [String: Task<Void, Never>] = [:]
    private var speechRequest: String?
    private var currentTurn: String?
    private var botSpeakingTurn: String?
    private var sessionTask: Task<Void, Never>?
    private var captureTask: Task<Void, Never>?
    private var stopTask: Task<Void, Never>?
    private var sessionGeneration = UUID()
    private var captureGeneration = UUID()
    private var inputReady = false

    init(synthesizerFactory: @escaping (TTSProviderID) throws -> any SpeechSynthesizer = SpeechSynthesizers.make) {
        self.synthesizerFactory = synthesizerFactory
        let audio = VoiceAudioEngine()
        self.audio = audio
        speech = AppleSpeechRecognizer(audio: audio)
        player = PCMPlayer(audio: audio)
        #if ENABLE_PHONON
        if selectedProvider == .phonon { hasKey = VoiceSettings.validKey(VoiceSettings.loadKey()) }
        #endif
        speech.onTranscript = { [weak self] text, final, start, end in
            guard let self, active, !muted, inputReady else { return }
            sendSession(["type": "transcription", "text": text, "final": final, "start": start, "end": end])
        }
        speech.onVAD = { [weak self] confidence, time, volume in
            guard let self, active, !muted, inputReady else { return }
            sendSession(["type": "vad", "confidence": confidence, "time": time, "volume": volume])
        }
        speech.onLevel = { [weak self] value in
            guard let self, active, !muted, inputReady else { return }
            userLevel = value
        }
        speech.onError = { [weak self] message in self?.fail(message) }
        player.onLevel = { [weak self] value in
            guard let self, active else { return }
            botLevel = value
        }
        player.onSpeakingChanged = { [weak self] speaking in
            guard let self else { return }
            botAudioPlaying = active && speaking
            guard let request = speechRequest else { return }
            sendSession(["type": "playback", "request": request, "speaking": speaking])
        }
        runtime.start { [weak self] json in
            Task { @MainActor in self?.handle(json) }
        }
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--verify-voice-settings") {
            print("TTS_PROVIDER: \(selectedProvider.rawValue), VOICE: \(selectedVoice), READY: \(!needsVoiceSetup)")
        }
        if ProcessInfo.processInfo.arguments.contains("--verify-pocket-tts") {
            Task { await TTSDiagnostics.run() }
        }
        #endif
        NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification,
            object: nil, queue: .main) { [weak self] notification in
                guard let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                      AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
                Task { @MainActor in self?.stop() }
            }
        NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification,
            object: nil, queue: .main) { [weak self] notification in
                guard let raw = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                      AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable else { return }
                Task { @MainActor in self?.stop() }
            }
    }

    func saveVoiceConfiguration(_ configuration: VoiceConfiguration, key: String = "") throws {
        guard !active else { throw VoiceError(message: "End the conversation before changing voices.") }
        let provider = configuration.effectiveProvider
        guard TTSProviderID.available.contains(configuration.provider),
              provider.voices.contains(configuration.voice(for: provider)) else {
            throw VoiceError(message: "Choose an available voice.")
        }
        #if ENABLE_PHONON
        if provider == .phonon {
            let value = key.trimmingCharacters(in: .whitespacesAndNewlines)
            guard value.isEmpty || VoiceSettings.validKey(value) else {
                throw VoiceError(message: "Use a Phonon key starting with gsk_ followed by 64 lowercase hexadecimal characters.")
            }
            try VoiceSettings.saveKey(value)
            hasKey = !value.isEmpty
        }
        #endif
        try VoiceSettings.save(configuration)
        voiceConfiguration = configuration
    }

    private func send(_ event: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: event),
              let json = String(data: data, encoding: .utf8) else { return }
        runtime.sendJSON(json)
    }

    private func sendSession(_ event: [String: Any]) {
        send(event.merging(["session": sessionGeneration.uuidString]) { _, session in session })
    }

    func start() {
        guard ready, !active else { return }
        error = nil
        if needsVoiceSetup { showSettings = true; return }
        availability = AppleLanguageModel.unavailableReason
        if let availability { error = availability; return }
        let provider = selectedProvider
        let voice = selectedVoice
        active = true
        muted = false
        userLevel = 0
        botLevel = 0
        inputReady = false
        phase = .preparing
        let generation = UUID()
        sessionGeneration = generation
        let previousStop = stopTask
        sessionTask = Task {
            await previousStop?.value
            do {
                try Task.checkCancellation()
                guard active, sessionGeneration == generation else { return }
                let session = AVAudioSession.sharedInstance()
                try session.setCategory(.playAndRecord, mode: .voiceChat,
                                        options: [.defaultToSpeaker, .allowBluetoothHFP])
                try session.setActive(true)
                let synthesizer = try synthesizerFactory(provider)
                self.synthesizer = synthesizer
                try await synthesizer.prepare(voice: voice)
                try Task.checkCancellation()
                guard active, sessionGeneration == generation else { return }
                sendSession(["type": "start", "provider": provider.rawValue])
                try await speech.start()
                try Task.checkCancellation()
                guard active, sessionGeneration == generation else { return }
                inputReady = true
                phase = .listening
            } catch is CancellationError { }
            catch { if sessionGeneration == generation { fail(error.localizedDescription) } }
        }
    }

    func primaryAction() {
        switch phase {
        case .idle: start()
        case .thinking, .speaking: interrupt()
        case .listening: sendSession(["type": "end_turn"])
        case .muted: toggleMute()
        case .preparing: break
        }
    }

    func interrupt() {
        guard active else { return }
        markInterrupted()
        cancelRequests()
        sendSession(["type": "interrupt"])
        currentTurn = nil
        partial = ""
        setSpeaking(role: "user", speaking: false)
        setSpeaking(role: "bot", speaking: false)
        botSpeakingTurn = nil
        phase = muted ? .muted : .listening
    }

    func toggleMute() {
        guard active, phase != .preparing else { return }
        muted.toggle()
        userLevel = 0
        partial = ""
        inputReady = false
        let isMuted = muted
        let generation = sessionGeneration
        let captureGeneration = UUID()
        self.captureGeneration = captureGeneration
        // Mute takes effect in Python immediately. Unmute must wait for the
        // native input clock to restart, otherwise queued capture transitions
        // can reset its timestamps after Pipecat has already accepted new audio.
        if isMuted {
            sendSession(["type": "mute"])
            setSpeaking(role: "user", speaking: false)
        }
        if !botSpeaking && phase != .thinking { phase = isMuted ? .muted : .listening }
        let previous = captureTask
        captureTask = Task {
            await previous?.value
            guard active, sessionGeneration == generation,
                  self.captureGeneration == captureGeneration else { return }
            do {
                try Task.checkCancellation()
                try await speech.setMuted(isMuted)
                try Task.checkCancellation()
                guard active, sessionGeneration == generation,
                      self.captureGeneration == captureGeneration else { return }
                if !isMuted {
                    sendSession(["type": "unmute"])
                    inputReady = true
                }
            }
            catch is CancellationError { }
            catch { if sessionGeneration == generation { fail(error.localizedDescription) } }
        }
    }

    func stop() {
        if ready { sendSession(["type": "stop"]) }
        active = false
        muted = false
        inputReady = false
        captureGeneration = UUID()
        sessionGeneration = UUID()
        sessionTask?.cancel()
        let previousPreparation = sessionTask
        sessionTask = nil
        let previousSynthesizer = synthesizer
        synthesizer = nil
        captureTask?.cancel()
        let previousCapture = captureTask
        captureTask = nil
        markInterrupted(reason: .ended)
        cancelRequests()
        currentTurn = nil
        partial = ""
        userLevel = 0
        botLevel = 0
        phase = .idle
        setSpeaking(role: "user", speaking: false)
        setSpeaking(role: "bot", speaking: false)
        botSpeakingTurn = nil
        let previousStop = stopTask
        stopTask = Task {
            await previousStop?.value
            await previousCapture?.value
            await speech.stop()
            audio.stop()
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            await previousPreparation?.value
            await previousSynthesizer?.unload()
        }
    }

    func clearConversation() {
        stop()
        messages.removeAll()
        speechEvents.removeAll()
        error = nil
        send(["type": "reset"])
    }

    private func fail(_ message: String) {
        stop()
        error = message
    }

    private func markInterrupted(reason: TranscriptInterruption = .user) {
        guard let currentTurn,
              phase == .thinking || phase == .speaking else { return }
        TranscriptMessage.interrupt(&messages, turn: currentTurn, reason: reason)
    }

    private func cancelRequests() {
        for task in requests.values { task.cancel() }
        requests.removeAll()
        player.stop()
        botLevel = 0
        speechRequest = nil
    }

    private func setSpeaking(role: String, speaking: Bool) {
        if role == "user" {
            if !speaking { userLevel = 0 }
            guard userSpeaking != speaking else { return }
            userSpeaking = speaking
        } else {
            if !speaking { botLevel = 0 }
            guard botSpeaking != speaking else { return }
            botSpeaking = speaking
        }
        speechEvents.append(SpeechActivityEvent(role: role, speaking: speaking))
        if speechEvents.count > 32 { speechEvents.removeFirst(speechEvents.count - 32) }
    }

    private func handle(_ json: String) {
        guard let data = json.data(using: .utf8) else { return }
        if let event = try? JSONDecoder().decode(RTVIEnvelope.self, from: data), event.type == "rtvi" {
            handleRTVI(event)
            return
        }
        guard let event = try? JSONDecoder().decode(NativeEvent.self, from: data) else {
            fail("The Python bridge returned an invalid event.")
            return
        }
        if event.type != "request", let session = event.session,
           session != sessionGeneration.uuidString { return }
        switch event.type {
        case "ready":
            ready = true
            print("PIPECAT_PYTHON_READY")
        case "user_turn":
            guard active, !muted, event.session == sessionGeneration.uuidString,
                  let turn = event.turn else { return }
            markInterrupted()
            cancelRequests()
            currentTurn = turn
            partial = ""
            phase = .listening
        case "error":
            if let session = event.session, session != sessionGeneration.uuidString { return }
            if event.turn == nil || event.turn == currentTurn { fail(event.message ?? "The pipeline failed.") }
        case "cancel_request":
            if let id = event.request {
                requests.removeValue(forKey: id)?.cancel()
                if speechRequest == id { player.stop(); speechRequest = nil }
            }
        case "cancel_turn":
            if let turn = event.turn, turn == currentTurn { cancelRequests() }
        case "state":
            guard active, event.session == sessionGeneration.uuidString,
                  event.turn == currentTurn else { return }
            if event.state == "listening" { phase = muted ? .muted : .listening }
        case "request":
            guard active, event.turn == currentTurn, event.session == sessionGeneration.uuidString else {
                if let id = event.request { send(["type": "result", "request": id, "error": "Turn was cancelled"]) }
                return
            }
            perform(event)
        default: fail("Unknown Python event: \(event.type)")
        }
    }

    private func handleRTVI(_ event: RTVIEnvelope) {
        guard active, event.session == sessionGeneration.uuidString else { return }
        // Playback stop for an interrupted turn can arrive after the next user
        // turn. It must clear its old indicator without stopping a newer reply.
        if event.message.type == "bot-stopped-speaking" {
            if event.turn == botSpeakingTurn {
                setSpeaking(role: "bot", speaking: false)
                botSpeakingTurn = nil
            }
            return
        }
        guard event.turn == currentTurn else { return }
        switch event.message.type {
        case "user-started-speaking", "vad-user-started-speaking":
            guard !muted, inputReady else { return }
            setSpeaking(role: "user", speaking: true)
            phase = .listening
        case "vad-user-stopped-speaking":
            setSpeaking(role: "user", speaking: false)
        case "user-stopped-speaking":
            setSpeaking(role: "user", speaking: false)
            let hasTranscript = !partial.isEmpty || messages.contains {
                $0.id == (event.turn ?? "") + "-user" && !$0.text.isEmpty
            }
            if !muted, phase == .listening, hasTranscript { phase = .thinking }
        case "bot-started-speaking":
            botSpeakingTurn = event.turn
            setSpeaking(role: "bot", speaking: true)
            phase = .speaking
        case "bot-llm-started": phase = .thinking
        case "bot-llm-text":
            appendTranscript(turn: event.turn, role: "assistant", text: event.message.data?.text ?? "", final: false)
        case "bot-llm-stopped":
            if let turn = event.turn, let index = messages.firstIndex(where: { $0.id == turn + "-assistant" }) {
                messages[index].isFinal = true
            }
        case "user-transcription":
            guard !muted, inputReady else { return }
            let text = event.message.data?.text ?? ""
            if event.message.data?.final == true {
                partial = ""
                appendTranscript(turn: event.turn, role: "user", text: text, final: true)
            } else { partial = text }
        default: break
        }
    }

    private func appendTranscript(turn: String?, role: String, text: String, final: Bool) {
        guard let turn, !text.isEmpty else { return }
        let id = turn + "-" + role
        if let index = messages.firstIndex(where: { $0.id == id }) {
            let separator = role == "user" && !messages[index].text.hasSuffix(" ") ? " " : ""
            messages[index].text += separator + text
            messages[index].isFinal = final
        } else {
            messages.append(TranscriptMessage(id: id, role: role, text: text, isFinal: final))
        }
    }

    private func perform(_ event: NativeEvent) {
        guard let id = event.request, let turn = event.turn else { return }
        let generation = sessionGeneration
        requests[id] = Task {
            defer {
                requests.removeValue(forKey: id)
                if speechRequest == id { speechRequest = nil }
            }
            do {
                try Task.checkCancellation()
                guard active, sessionGeneration == generation, currentTurn == turn else { throw CancellationError() }
                switch event.operation {
                case "generate":
                    phase = .thinking
                    try await AppleLanguageModel.generate(prompt: event.prompt ?? "",
                        instructions: event.instructions ?? "", history: event.history ?? []) { delta in
                            guard self.active, self.sessionGeneration == generation, self.currentTurn == turn,
                                  !Task.isCancelled else { return }
                            self.send(["type": "result", "request": id, "delta": delta])
                        }
                case "speak":
                    if let provider = event.provider, provider != selectedProvider.rawValue {
                        throw VoiceError(message: "The pipeline requested a different speech provider. Restart the conversation.")
                    }
                    speechRequest = id
                    guard let synthesizer else { throw VoiceError(message: "The speech engine is not ready.") }
                    let synthesis = try await synthesizer.synthesize(event.text ?? "")
                    do {
                        try await withTaskCancellationHandler {
                            for try await samples in synthesis.samples {
                                try Task.checkCancellation()
                                guard active, sessionGeneration == generation, currentTurn == turn,
                                      speechRequest == id else { throw CancellationError() }
                                try player.enqueue(samples)
                            }
                            try await player.finish()
                        } onCancel: { synthesis.cancel() }
                        await synthesis.waitForCompletion()
                    } catch {
                        synthesis.cancel()
                        await synthesis.waitForCompletion()
                        throw error
                    }
                case "finalize_asr":
                    guard !muted else { throw CancellationError() }
                    try await speech.finalize()
                default: throw VoiceError(message: "Unknown native operation.")
                }
                try Task.checkCancellation()
                guard active, sessionGeneration == generation, currentTurn == turn else { throw CancellationError() }
                send(["type": "result", "request": id, "done": true])
            } catch is CancellationError { }
            catch {
                if active, sessionGeneration == generation, currentTurn == turn {
                    send(["type": "result", "request": id, "error": error.localizedDescription])
                }
            }
        }
    }
}
