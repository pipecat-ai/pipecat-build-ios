import AVFoundation
import Foundation
import Observation

struct TranscriptMessage: Identifiable {
    let id: String
    let role: String
    var text: String
    var isFinal: Bool
    var interrupted = false
}

private struct NativeEvent: Decodable {
    let type: String
    var operation: String?
    var request: String?
    var turn: String?
    var state: String?
    var role: String?
    var text: String?
    var final: Bool?
    var prompt: String?
    var instructions: String?
    var history: [HistoryMessage]?
    var message: String?
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
    var level = 0.0
    var active = false
    var muted = false
    var ready = false
    var error: String?
    var showSettings = false
    var hasKey = false
    var selectedVoice = UserDefaults.standard.string(forKey: "voice") ?? "Marlowe"
    var availability: String? = AppleLanguageModel.unavailableReason

    private let runtime = PCPythonRuntime()
    private let speech = AppleSpeechRecognizer()
    private let phonon = PhononSynthesizer()
    private let player = PCMPlayer()
    private var requests: [String: Task<Void, Never>] = [:]
    private var speechRequest: String?
    private var currentTurn: String?
    private var sessionTask: Task<Void, Never>?
    private var sessionGeneration = UUID()

    init() {
        hasKey = VoiceSettings.validKey(VoiceSettings.loadKey())
        speech.onPartial = { [weak self] text in self?.partial = text }
        speech.onFinal = { [weak self] text in self?.submit(text) }
        speech.onLevel = { [weak self] level in self?.level = level }
        speech.onError = { [weak self] message in self?.fail(message) }
        player.onLevel = { [weak self] level in self?.level = level }
        runtime.start { [weak self] json in
            Task { @MainActor in self?.handle(json) }
        }
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--verify-voice-settings") {
            print(hasKey ? "PHONON_VOICE_CONFIGURED" : "PHONON_VOICE_KEY_MISSING")
        }
        // This checks model/tokenizer loading only. It never synthesizes speech
        // or calls Gradium with the intentionally inert test credential.
        if ProcessInfo.processInfo.arguments.contains("--verify-model-load") {
            Task {
                do {
                    guard let root = Bundle.main.url(forResource: "phonon", withExtension: nil) else {
                        throw VoiceError(message: "Missing bundled model")
                    }
                    let verifier = PhononSynthesizer()
                    try await verifier.load(root: root, voice: "Marlowe", key: "gsk_" + String(repeating: "0", count: 64))
                    print("PHONON_MODEL_LOADED")
                } catch { print("PHONON_MODEL_LOAD_FAILED: \(error.localizedDescription)") }
            }
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

    private func send(_ event: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: event),
              let json = String(data: data, encoding: .utf8) else { return }
        runtime.sendJSON(json)
    }

    func start() {
        guard ready, !active else { return }
        error = nil
        let key = VoiceSettings.loadKey()
        guard VoiceSettings.validKey(key) else { showSettings = true; return }
        availability = AppleLanguageModel.unavailableReason
        if let availability { error = availability; return }
        guard let root = Bundle.main.url(forResource: "phonon", withExtension: nil) else {
            fail("Phonon’s model files are missing from the app bundle.")
            return
        }
        active = true
        muted = false
        phase = .preparing
        let generation = UUID()
        sessionGeneration = generation
        sessionTask = Task {
            do {
                let audio = AVAudioSession.sharedInstance()
                try audio.setCategory(.playAndRecord, mode: .default,
                                      options: [.defaultToSpeaker, .allowBluetoothHFP])
                try audio.setActive(true)
                try await phonon.load(root: root, voice: selectedVoice, key: key)
                try Task.checkCancellation()
                guard active, sessionGeneration == generation else { return }
                try await speech.start()
                guard active, sessionGeneration == generation else { return }
                phase = .listening
            } catch is CancellationError { }
            catch { if sessionGeneration == generation { fail(error.localizedDescription) } }
        }
    }

    private func listen() {
        guard active else { return }
        if muted { phase = .muted; return }
        phase = .preparing
        partial = ""
        let generation = sessionGeneration
        sessionTask?.cancel()
        sessionTask = Task {
            do {
                try await speech.start()
                try Task.checkCancellation()
                guard active, sessionGeneration == generation, !muted else { return }
                phase = .listening
            } catch is CancellationError { }
            catch { if sessionGeneration == generation { fail(error.localizedDescription) } }
        }
    }

    private func submit(_ text: String) {
        guard active, !muted else { return }
        partial = ""
        guard !text.isEmpty else { listen(); return }
        let turn = UUID().uuidString
        currentTurn = turn
        phase = .thinking
        send(["type": "transcription", "text": text, "turn": turn])
    }

    func primaryAction() {
        switch phase {
        case .idle: start()
        case .thinking, .speaking: interrupt()
        case .listening:
            Task { await speech.finishTurn() }
        case .muted: toggleMute()
        case .preparing: break
        }
    }

    func interrupt() {
        markInterrupted()
        cancelRequests()
        currentTurn = nil
        send(["type": "interrupt"])
        muted = false
        listen()
    }

    func toggleMute() {
        guard active, phase != .preparing else { return }
        muted.toggle()
        if muted {
            if phase == .listening || phase == .preparing {
                sessionTask?.cancel()
                Task { await speech.stop() }
                partial = ""
                phase = .muted
            }
        } else if phase == .muted { listen() }
    }

    func stop() {
        sessionGeneration = UUID()
        active = false
        muted = false
        sessionTask?.cancel()
        sessionTask = nil
        markInterrupted()
        cancelRequests()
        currentTurn = nil
        partial = ""
        level = 0
        phase = .idle
        if ready { send(["type": "stop"]) }
        Task {
            await speech.stop()
            if !active { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
        }
    }

    func clearConversation() {
        stop()
        messages.removeAll()
        error = nil
        send(["type": "reset"])
    }

    private func fail(_ message: String) {
        stop()
        error = message
    }

    private func markInterrupted() {
        guard let currentTurn,
              let index = messages.firstIndex(where: { $0.id == currentTurn + "-assistant" }) else { return }
        messages[index].interrupted = true
        messages[index].isFinal = true
    }

    private func cancelRequests() {
        for task in requests.values { task.cancel() }
        requests.removeAll()
        speechRequest = nil
        player.stop()
    }

    private func handle(_ json: String) {
        guard let data = json.data(using: .utf8),
              let event = try? JSONDecoder().decode(NativeEvent.self, from: data) else {
            fail("The Python bridge returned an invalid event.")
            return
        }
        switch event.type {
        case "ready":
            ready = true
            print("PIPECAT_PYTHON_READY")
        case "error":
            if event.turn == nil || event.turn == currentTurn { fail(event.message ?? "The pipeline failed.") }
        case "cancel_request":
            if let id = event.request {
                requests.removeValue(forKey: id)?.cancel()
                if speechRequest == id { player.stop(); speechRequest = nil }
            }
        case "cancel_turn":
            // The host already stops immediately on user interaction. An old
            // Python cancellation must not stop a newer turn or microphone.
            if let turn = event.turn, turn == currentTurn { cancelRequests() }
        case "transcript":
            guard active, event.turn == currentTurn, let turn = event.turn,
                  let role = event.role, let text = event.text else { return }
            let id = turn + "-" + role
            if let index = messages.firstIndex(where: { $0.id == id }) {
                messages[index].text = text
                messages[index].isFinal = event.final ?? false
            } else {
                messages.append(TranscriptMessage(id: id, role: role, text: text, isFinal: event.final ?? false))
            }
        case "state":
            guard active, event.turn == currentTurn, let state = event.state else { return }
            if state == "listening" { currentTurn = nil; listen() }
            else if let next = VoicePhase(rawValue: state) { phase = next }
        case "request":
            guard active, event.turn == currentTurn else {
                if let id = event.request { send(["type": "result", "request": id, "error": "Turn was cancelled"]) }
                return
            }
            perform(event)
        default: fail("Unknown Python event: \(event.type)")
        }
    }

    private func perform(_ event: NativeEvent) {
        guard let id = event.request, let turn = event.turn else { return }
        requests[id] = Task {
            defer { requests.removeValue(forKey: id) }
            do {
                if event.operation == "generate" {
                    try await AppleLanguageModel.generate(prompt: event.prompt ?? "",
                        instructions: event.instructions ?? "", history: event.history ?? []) { delta in
                            self.send(["type": "result", "request": id, "delta": delta])
                        }
                } else if event.operation == "speak" {
                    speechRequest = id
                    for try await samples in phonon.stream(event.text ?? "") {
                        try Task.checkCancellation()
                        guard currentTurn == turn, speechRequest == id else { throw CancellationError() }
                        try player.enqueue(samples)
                    }
                    try await player.finish()
                    if speechRequest == id { speechRequest = nil }
                } else { throw VoiceError(message: "Unknown native operation.") }
                try Task.checkCancellation()
                send(["type": "result", "request": id, "done": true])
            } catch is CancellationError { }
            catch {
                if currentTurn == turn { send(["type": "result", "request": id, "error": error.localizedDescription]) }
            }
        }
    }
}
