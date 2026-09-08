import SwiftUI

struct ConversationView: View {
    @Bindable var model: ConversationModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.colorScheme) private var colorScheme

    private var connecting: Bool { !model.ready || model.phase == .preparing }
    private var hasConversation: Bool { !model.messages.isEmpty || !model.partial.isEmpty }
    private var thinking: Bool {
        model.phase == .thinking && model.messages.last?.role != "assistant"
    }

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                header
                presence(compact: geometry.size.height < 620 || dynamicTypeSize.isAccessibilitySize)
                conversationHeading
                ConversationChat(messages: model.transcriptMessages, thinking: thinking,
                                 active: model.active && !connecting)
                bottomControls
            }
            .frame(maxWidth: 720).frame(maxWidth: .infinity)
        }
        .background {
            Color(uiColor: .systemGroupedBackground).ignoresSafeArea()
            LinearGradient(colors: [VoicePalette.surface.opacity(0.9), VoicePalette.surface.opacity(0)],
                           startPoint: .top, endPoint: .center).ignoresSafeArea()
        }
        .tint(VoicePalette.blue)
        .sensoryFeedback(.selection, trigger: model.muted)
        .sensoryFeedback(.impact(weight: .light), trigger: model.active)
        .sheet(isPresented: $model.showSettings) { SettingsView(model: model) }
    }

    private var bottomControls: some View {
        VStack(spacing: 10) {
            if let error = model.error { errorBanner(error) }
            controlDock
        }
        .frame(maxWidth: 520)
        .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 10)
        .frame(maxWidth: .infinity)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image("PipecatWordmark").resizable().scaledToFit()
                .frame(width: 133, height: 30)
                .foregroundStyle(.primary)
                .accessibilityLabel("Pipecat")
            Spacer(minLength: 12)
            GlassEffectContainer(spacing: 10) {
                HStack(spacing: 10) {
                    Button { model.clearConversation() } label: {
                        Image(systemName: "square.and.pencil").frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("New conversation")
                    .disabled(!hasConversation && !model.active)
                    Button { model.showSettings = true } label: {
                        Image(systemName: "slider.horizontal.3").frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("Voice settings")
                    .disabled(model.active)
                }
                .font(.system(size: 17, weight: .medium))
                .buttonStyle(.glass).buttonBorderShape(.circle)
            }
        }
        .padding(.horizontal, 22).padding(.top, 8).padding(.bottom, 6)
    }

    @ViewBuilder
    private func presence(compact: Bool) -> some View {
        if compact {
            HStack(spacing: 12) {
                BotVoiceOrb(level: model.botLevel, speaking: model.botAudioPlaying)
                    .frame(width: 64, height: 64)
                statusText(alignment: .leading)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 24).padding(.vertical, 8)
        } else {
            VStack(spacing: 4) {
                BotVoiceOrb(level: model.botLevel, speaking: model.botAudioPlaying)
                    .frame(width: hasConversation ? 144 : 184, height: hasConversation ? 144 : 184)
                statusText(alignment: .center)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, hasConversation ? 4 : 12).padding(.bottom, 22)
            .animation(reduceMotion ? nil : .smooth(duration: 0.35), value: hasConversation)
        }
    }

    private func statusText(alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 6) {
            Text(model.ready ? model.phase.title : "Waking up Pipecat")
                .font(.title3.weight(.semibold)).fontDesign(.rounded)
                .contentTransition(.opacity)
            Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(alignment == .center ? .center : .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, alignment == .center ? 20 : 0)
        .accessibilityElement(children: .combine)
    }

    private var conversationHeading: some View {
        ViewThatFits(in: .horizontal) {
            HStack {
                Text("Conversation").font(.subheadline.weight(.semibold))
                Spacer(minLength: 16)
                voiceLabel
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Conversation").font(.subheadline.weight(.semibold))
                voiceLabel
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 26).padding(.bottom, 8)
    }

    private var voiceLabel: some View {
        HStack(spacing: 5) {
            Circle().fill(model.active ? VoicePalette.green : Color.secondary.opacity(0.45))
                .frame(width: 5, height: 5).accessibilityHidden(true)
            Text("\(model.selectedProvider.voiceTitle(model.selectedVoice)) · EN")
                .font(.caption)
        }.foregroundStyle(.secondary)
            .accessibilityLabel("\(model.selectedProvider.voiceTitle(model.selectedVoice)) voice, English")
    }

    private var controlDock: some View {
        VStack(spacing: 10) {
            HStack(spacing: 20) {
                if model.active {
                    secondaryControl(title: actionTitle, icon: actionIcon,
                                     disabled: connecting || model.phase == .muted,
                                     action: model.primaryAction)
                        .accessibilityIdentifier("primaryVoiceControl")
                        .accessibilityLabel(model.phase == .thinking || model.phase == .speaking
                                            ? "Interrupt Pipecat’s reply" : "Send your turn now")
                    microphone
                    secondaryControl(title: "End", icon: "phone.down.fill", disabled: false,
                                     action: model.stop)
                        .accessibilityLabel("End conversation")
                } else {
                    microphone
                    VStack(alignment: .leading, spacing: 4) {
                        Text(connecting ? "Getting ready" : model.needsVoiceSetup ? "Set up your voice" : "Let’s talk")
                            .font(.headline).fontDesign(.rounded)
                        Text(connecting ? "Just a moment…" : "Tap the mic to begin")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
            }
            if model.active {
                Text(microphoneStatus).font(.caption).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 14)
        .background {
            if reduceTransparency {
                RoundedRectangle(cornerRadius: 32).fill(VoicePalette.surface)
                    .overlay(RoundedRectangle(cornerRadius: 32).stroke(Color.primary.opacity(0.10)))
            }
        }
        .glassEffect(reduceTransparency ? .identity : .regular, in: .rect(cornerRadius: 32))
    }

    private var microphone: some View {
        MicrophoneControl(level: model.userLevel, muted: model.muted, connecting: connecting,
                          active: model.active) {
            if model.active { model.toggleMute() } else { model.start() }
        }
    }

    private func secondaryControl(title: String, icon: String, disabled: Bool,
                                  action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 7) {
                Image(systemName: icon).font(.system(size: 20, weight: .medium))
                    .frame(height: 24)
                Text(title).font(.caption.weight(.medium))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, minHeight: 60)
            .contentShape(Rectangle())
        }
        .buttonStyle(VoicePressStyle())
        .foregroundStyle(disabled ? Color.secondary.opacity(0.4) : .primary)
        .disabled(disabled)
    }

    private func errorBanner(_ error: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").padding(.top, 2)
            Text(error).font(.footnote).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            Button { model.error = nil } label: {
                Image(systemName: "xmark").font(.caption.weight(.semibold))
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }.accessibilityLabel("Dismiss error")
        }
        .foregroundStyle(colorScheme == .dark ? Color(red: 1, green: 0.55, blue: 0.58) : VoicePalette.red)
        .padding(.leading, 14).padding(.vertical, 8)
        .background(VoicePalette.surface, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(VoicePalette.red.opacity(0.2)))
    }

    private var subtitle: String {
        if !model.ready { return "Your voice space is almost ready." }
        switch model.phase {
        case .idle: return model.needsVoiceSetup ? "Choose a voice to get started." : "A space to talk things through."
        case .preparing: return "First use may take a moment."
        case .listening: return model.userSpeaking ? "Keep going. I’m with you." : "Speak naturally. Pause to send."
        case .thinking, .speaking: return model.muted ? "You’re muted. You can still listen." : "You can jump in anytime."
        case .muted: return "Take your time. Unmute when you’re ready."
        }
    }

    private var microphoneStatus: String {
        if connecting { return "Connecting · just a moment" }
        if model.muted { return "Microphone off · tap the red mic to unmute" }
        return model.userSpeaking ? "Hearing you · pause to send" : "Microphone on · speak anytime"
    }

    private var actionTitle: String {
        if model.phase == .thinking || model.phase == .speaking {
            return dynamicTypeSize.isAccessibilitySize ? "Stop reply" : "Interrupt"
        }
        return dynamicTypeSize.isAccessibilitySize ? "Send" : "Send now"
    }

    private var actionIcon: String {
        model.phase == .thinking || model.phase == .speaking ? "arrow.uturn.backward" : "arrow.up"
    }
}

private struct SettingsView: View {
    @Bindable var model: ConversationModel
    @Environment(\.dismiss) private var dismiss
    @State private var configuration: VoiceConfiguration
    @State private var key = ""
    @State private var hasLoadedKey = false
    @State private var error: String?

    init(model: ConversationModel) {
        self.model = model
        var draft = model.voiceConfiguration
        draft.provider = draft.effectiveProvider
        _configuration = State(initialValue: draft)
    }

    private var provider: TTSProviderID { configuration.effectiveProvider }
    private var voiceBinding: Binding<String> {
        Binding(get: { configuration.voice(for: provider) },
                set: { configuration.voices[provider.rawValue] = $0 })
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Your voice") {
                    if TTSProviderID.available.count > 1 {
                        Picker("Speech provider", selection: $configuration.provider) {
                            ForEach(TTSProviderID.available, id: \.self) { Text($0.title).tag($0) }
                        }
                    }
                    Picker("Voice", selection: voiceBinding) {
                        ForEach(provider.voices, id: \.self) { Text(provider.voiceTitle($0)).tag($0) }
                    }
                    LabeledContent("Language", value: "English (US)")
                    if model.active { Text("End the conversation before changing voices.").font(.footnote) }
                }.disabled(model.active)
                #if ENABLE_PHONON
                if provider == .phonon {
                    Section {
                        SecureField("Gradium key (gsk_…)", text: $key)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                    } header: { Text("Gradium Phonon") } footer: {
                        Text("Stored securely in this device’s Keychain. The supplied Phonon runtime requires a Gradium key and reports session data, including the text it speaks, to Gradium. Speech audio is generated on this device.")
                    }.disabled(model.active)
                }
                #endif
                Section("On this device") {
                    LabeledContent("Speech recognition", value: "Apple Speech")
                    LabeledContent("Language model", value: "Apple Foundation Models")
                    LabeledContent("Speech synthesis", value: provider.title)
                    if provider == .pocketTTS {
                        Text("PocketTTS generates speech on this device using bundled models. No account or key is required.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    if let availability = model.availability {
                        Label(availability, systemImage: "info.circle")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Text("Conversations stay in memory and clear when the app closes. Apple Intelligence must be enabled. Apple speech assets may download on first use.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Credits") {
                    Text("PocketTTS by Kyutai. Core ML conversion by Fluid Inference, licensed under CC BY 4.0. FluidAudio is licensed under Apache 2.0.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Link("PocketTTS model and license", destination: URL(string: "https://huggingface.co/FluidInference/pocket-tts-coreml")!)
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }.navigationTitle("Voice settings").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            do {
                                try model.saveVoiceConfiguration(configuration, key: key)
                                dismiss()
                            } catch { self.error = error.localizedDescription }
                        }.disabled(model.active)
                    }
                }
                .onAppear { loadPrivateKey() }
                .onChange(of: provider) { _, _ in loadPrivateKey() }
        }.tint(VoicePalette.blue)
    }

    private func loadPrivateKey() {
        #if ENABLE_PHONON
        if provider == .phonon && !hasLoadedKey {
            key = VoiceSettings.loadKey()
            hasLoadedKey = true
        }
        #endif
    }
}
