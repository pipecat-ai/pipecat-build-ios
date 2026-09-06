import SwiftUI

private let ink = Color(red: 24 / 255, green: 24 / 255, blue: 27 / 255)
private let paper = Color(red: 250 / 255, green: 250 / 255, blue: 250 / 255)
private let accent = ink
private let live = Color(red: 21 / 255, green: 128 / 255, blue: 61 / 255)

struct ConversationView: View {
    @Bindable var model: ConversationModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 9) {
                Image("PipecatWordmark")
                    .resizable().scaledToFit().frame(width: 166, height: 35)
                    .accessibilityLabel("Pipecat")
                Spacer()
                Button { model.clearConversation() } label: {
                    Image(systemName: "square.and.pencil").frame(width: 44, height: 44)
                }.accessibilityLabel("New conversation").disabled(model.messages.isEmpty)
                Button { model.showSettings = true } label: {
                    Image(systemName: "slider.horizontal.3").frame(width: 44, height: 44)
                }.accessibilityLabel("Voice settings").disabled(model.active)
            }
            .font(.system(size: 18)).padding(.horizontal, 24).padding(.top, 12)

            VStack(spacing: 16) {
                HStack(spacing: 6) {
                    Circle().fill(model.active ? live : ink.opacity(0.3)).frame(width: 6, height: 6)
                    Text(model.active ? "CONVERSATION IN PROGRESS" : "YOUR PERSONAL VOICE SPACE")
                        .font(.system(size: 9, weight: .semibold, design: .monospaced)).tracking(1.6)
                }.foregroundStyle(ink.opacity(0.6))
                VoiceOrb(level: model.level, active: model.active, phase: model.phase,
                         reduceMotion: reduceMotion)
                    .frame(width: 150, height: 150)
                    .accessibilityHidden(true)
                VStack(spacing: 6) {
                    Text(model.ready ? model.phase.title : "Waking up Pipecat")
                        .font(.system(size: 25, weight: .medium, design: .serif))
                    Text(subtitle).font(.system(size: 12)).foregroundStyle(ink.opacity(0.52))
                        .multilineTextAlignment(.center)
                }
            }.padding(.top, 24).padding(.bottom, 24)

            HStack {
                Text("CONVERSATION").tracking(1.7)
                Spacer()
                Text("ENGLISH · \(model.selectedVoice.uppercased())").tracking(0.7)
            }.font(.system(size: 9, weight: .medium, design: .monospaced))
                .foregroundStyle(ink.opacity(0.45)).padding(.horizontal, 28).padding(.bottom, 12)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 23) {
                        if model.messages.isEmpty && model.partial.isEmpty {
                            VStack(alignment: .leading, spacing: 12) {
                                Image(systemName: "quote.opening").font(.title2).foregroundStyle(accent)
                                Text("A thought. A question.\nSee where it takes you.")
                                    .font(.system(size: 25, weight: .regular, design: .serif))
                                    .lineSpacing(3)
                                Text("Start talking and your conversation will appear here.")
                                    .font(.system(size: 13)).foregroundStyle(ink.opacity(0.5))
                            }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                                .background(.white, in: RoundedRectangle(cornerRadius: 20))
                                .overlay(RoundedRectangle(cornerRadius: 20).stroke(ink.opacity(0.07), lineWidth: 1))
                        }
                        ForEach(model.messages) { message in
                            TranscriptRow(message: message)
                        }
                        if !model.partial.isEmpty {
                            TranscriptRow(message: TranscriptMessage(id: "partial", role: "user",
                                                                    text: model.partial, isFinal: false))
                                .opacity(0.65)
                        }
                        if model.phase == .thinking && model.messages.last?.role == "user" {
                            HStack(spacing: 7) {
                                ProgressView().controlSize(.mini)
                                Text("Pipecat is thinking…").font(.system(size: 12))
                            }.foregroundStyle(ink.opacity(0.5))
                        }
                        Color.clear.frame(height: 1).id("latest")
                    }.padding(.horizontal, 24).padding(.vertical, 6)
                }.scrollDismissesKeyboard(.interactively)
                    .onChange(of: model.messages.last?.text) { _, _ in proxy.scrollTo("latest", anchor: .bottom) }
                    .onChange(of: model.partial) { _, _ in proxy.scrollTo("latest", anchor: .bottom) }
            }

            if let error = model.error {
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: "exclamationmark.circle")
                    Text(error).font(.system(size: 12))
                    Spacer(minLength: 0)
                    Button { model.error = nil } label: { Image(systemName: "xmark") }
                        .accessibilityLabel("Dismiss error")
                }.foregroundStyle(.red).padding(14)
                    .background(.red.opacity(0.05), in: RoundedRectangle(cornerRadius: 14))
                    .padding(.horizontal, 24).padding(.top, 8)
            }

            VStack(spacing: 15) {
                HStack(spacing: 27) {
                    Button { model.toggleMute() } label: {
                        Image(systemName: model.muted ? "mic.slash.fill" : "mic.fill")
                            .font(.system(size: 19)).frame(width: 50, height: 50)
                            .background(ink.opacity(0.06), in: Circle())
                    }.disabled(!model.active || model.phase == .preparing)
                        .accessibilityLabel(model.muted ? "Unmute microphone" : "Mute microphone")
                    Button { model.primaryAction() } label: {
                        HStack(spacing: 9) {
                            if model.phase == .preparing { ProgressView().tint(.white) }
                            else { Image(systemName: primaryIcon).font(.system(size: 17, weight: .semibold)) }
                            Text(primaryTitle).font(.system(size: 14, weight: .semibold))
                        }.frame(minWidth: 130, minHeight: 60).padding(.horizontal, 12)
                            .foregroundStyle(.white).background(accent, in: Capsule())
                    }.disabled(!model.ready || model.phase == .preparing)
                        .accessibilityIdentifier("primaryVoiceControl")
                    Button { model.stop() } label: {
                        Image(systemName: "phone.down.fill").font(.system(size: 19))
                            .frame(width: 50, height: 50).background(ink.opacity(0.06), in: Circle())
                    }.disabled(!model.active).accessibilityLabel("End conversation")
                }
                Text("PIPECAT VOICE")
                    .font(.system(size: 8, weight: .medium, design: .monospaced)).tracking(0.8)
                    .foregroundStyle(ink.opacity(0.4))
            }.padding(.top, 20).padding(.bottom, 18)
        }
        .foregroundStyle(ink).background(paper).tint(ink)
        .sheet(isPresented: $model.showSettings) { SettingsView(model: model) }
    }

    private var subtitle: String {
        switch model.phase {
        case .idle: model.hasKey ? "A familiar voice. A fresh perspective." : "Add your Gradium key to start a conversation."
        case .preparing: "Loading on-device models. First use may take a moment."
        case .listening: "Pause to send, or tap Send now."
        case .thinking, .speaking: "Tap to talk whenever you’re ready."
        case .muted: "Take your time. Tap to unmute."
        }
    }

    private var primaryTitle: String {
        switch model.phase {
        case .idle: model.hasKey ? "Let’s talk" : "Set up voice"
        case .preparing: "Getting ready"
        case .listening: "Send now"
        case .thinking, .speaking: "Tap to talk"
        case .muted: "Unmute"
        }
    }

    private var primaryIcon: String {
        switch model.phase {
        case .listening: "arrow.up"
        case .thinking, .speaking: "waveform"
        default: "mic.fill"
        }
    }
}

private struct TranscriptRow: View {
    let message: TranscriptMessage
    var body: some View {
        HStack(alignment: .top, spacing: 13) {
            Group {
                if message.role == "user" {
                    Image(systemName: "person.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(ink.opacity(0.6))
                } else {
                    Image("PipecatMark").resizable().scaledToFit()
                        .frame(width: 20, height: 12).foregroundStyle(ink)
                }
            }
            .frame(width: 33, height: 33)
            .background(ink.opacity(0.05), in: Circle())
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 7) {
                    Text(message.role == "user" ? "You" : "Pipecat")
                        .font(.system(size: 11, weight: .semibold))
                    if message.interrupted {
                        Text("Interrupted").font(.system(size: 9)).foregroundStyle(ink.opacity(0.4))
                    }
                }
                Text(message.text).font(.system(size: 15)).lineSpacing(5)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }.padding(.top, 4)
            Spacer(minLength: 0)
        }.accessibilityElement(children: .combine)
    }
}

private struct VoiceOrb: View {
    let level: Double
    let active: Bool
    let phase: VoicePhase
    let reduceMotion: Bool

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 24, paused: reduceMotion || !active)) { context in
            let t = reduceMotion ? 0 : context.date.timeIntervalSinceReferenceDate
            ZStack {
                Circle().stroke(ink.opacity(0.06), lineWidth: 1).padding(1)
                Circle().fill(ink).padding(9)
                Circle().stroke(.white.opacity(0.08), lineWidth: 1).padding(19)
                Image("PipecatMark").resizable().scaledToFit()
                    .frame(width: 83, height: 48).foregroundStyle(.white)
                    .offset(y: -13)
                HStack(spacing: 4) {
                    ForEach(0..<11) { index in
                        let wave = (sin(t * 3.2 + Double(index) * 0.65) + 1) / 2
                        let envelope = sin(Double(index + 1) / 12 * .pi)
                        let energy = active ? min(1, max(level, phase == .thinking ? 0.25 : 0.08)) : 0.0
                        Capsule().fill(.white.opacity(active ? 0.85 : 0.3))
                            .frame(width: 3, height: 3 + envelope * (3 + energy * 22 * wave))
                    }
                }.frame(height: 28).offset(y: 37)
            }.shadow(color: ink.opacity(0.09), radius: 12, y: 8)
        }
    }
}

private struct SettingsView: View {
    @Bindable var model: ConversationModel
    @Environment(\.dismiss) private var dismiss
    @State private var key = VoiceSettings.loadKey()
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Your voice") {
                    Picker("Voice", selection: $model.selectedVoice) {
                        ForEach(VoiceSettings.voices, id: \.self) { Text($0) }
                    }
                    LabeledContent("Language", value: "English (US)")
                }
                Section {
                    SecureField("Gradium key (gsk_…)", text: $key)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                } header: { Text("Gradium Phonon") } footer: {
                    Text("Stored securely in this device’s Keychain. The supplied Phonon runtime requires a Gradium key and reports session data, including the text it speaks, to Gradium. Speech audio is generated on this device.")
                }
                Section("On this device") {
                    LabeledContent("Speech recognition", value: "Apple Speech")
                    LabeledContent("Language model", value: "Apple Foundation Models")
                    LabeledContent("Speech synthesis", value: "Gradium Phonon")
                    if let availability = model.availability {
                        Label(availability, systemImage: "info.circle")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Text("Conversations stay in memory and clear when the app closes. Apple Intelligence must be enabled. Speech assets may download on first use.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }.navigationTitle("Voice settings").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") {
                            let value = key.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard value.isEmpty || VoiceSettings.validKey(value) else {
                                error = "Use a Phonon key starting with gsk_ followed by 64 lowercase hexadecimal characters."
                                return
                            }
                            do {
                                try VoiceSettings.saveKey(value)
                                UserDefaults.standard.set(model.selectedVoice, forKey: "voice")
                                model.hasKey = !value.isEmpty
                                dismiss()
                            } catch { self.error = error.localizedDescription }
                        }
                    }
                }
        }.tint(accent)
    }
}
