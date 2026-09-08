import SwiftUI

struct ConversationChat: View {
    let messages: [TranscriptMessage]
    let thinking: Bool
    let active: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var followsLatest = true
    @State private var nearBottom = true
    @State private var scrolling = false

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 18) {
                    if messages.isEmpty && !thinking {
                        emptyConversation
                    }
                    ForEach(messages) { message in
                        VStack(spacing: 12) {
                            if !message.text.isEmpty { TranscriptBubble(message: message) }
                            if let interruption = message.interruption {
                                InterruptionMarker(reason: interruption)
                            }
                        }.id(message.id)
                    }
                    if thinking {
                        HStack {
                            ThinkingIndicator()
                            Spacer(minLength: 50)
                        }
                    }
                    Color.clear.frame(height: 1).id("latest")
                }
                .padding(.horizontal, 22).padding(.top, 10).padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
            .scrollEdgeEffectStyle(.soft, for: .top)
            .defaultScrollAnchor(.bottom, for: .initialOffset)
            .defaultScrollAnchor(followsLatest ? .bottom : nil, for: .sizeChanges)
            .onAppear { proxy.scrollTo("latest", anchor: .bottom) }
            .onScrollGeometryChange(for: Bool.self) { geometry in
                geometry.contentSize.height - geometry.visibleRect.maxY < 60
            } action: { _, atBottom in
                nearBottom = atBottom
                if scrolling { followsLatest = atBottom }
            }
            .onScrollPhaseChange { _, phase in
                scrolling = phase == .tracking || phase == .interacting || phase == .decelerating
                if phase == .idle && nearBottom { followsLatest = true }
            }
            .onChange(of: messages) { _, newMessages in
                if newMessages.isEmpty { followsLatest = true }
                if followsLatest && !scrolling { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .onChange(of: thinking) { _, _ in
                if followsLatest && !scrolling { proxy.scrollTo("latest", anchor: .bottom) }
            }
            .overlay(alignment: .bottom) {
                if !followsLatest {
                    Button {
                        followsLatest = true
                        withAnimation(reduceMotion ? nil : .smooth(duration: 0.25)) {
                            proxy.scrollTo("latest", anchor: .bottom)
                        }
                    } label: {
                        Label("Latest", systemImage: "arrow.down")
                            .font(.subheadline.weight(.semibold))
                            .padding(.horizontal, 16).padding(.vertical, 11)
                    }
                    .buttonStyle(.plain)
                    .glassEffect(.regular.interactive(), in: Capsule())
                    .padding(.bottom, 8)
                    .accessibilityLabel("Scroll to latest message")
                }
            }
        }
        .accessibilityIdentifier("conversationTranscript")
    }

    private var emptyConversation: some View {
        VStack(spacing: 12) {
            Image(systemName: "quote.bubble")
                .font(.system(size: 27, weight: .light))
                .foregroundStyle(VoicePalette.violet.opacity(0.8))
                .accessibilityHidden(true)
            Text(active ? "Go ahead. I’m here." : "A little curiosity.\nA good conversation.")
                .font(.title2.weight(.medium)).fontDesign(.rounded)
                .multilineTextAlignment(.center)
            Text(active ? "Your words will appear as you speak." : "Ask a question, think out loud,\nor see where a thought takes you.")
                .font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).lineSpacing(3)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 26)
        .accessibilityElement(children: .combine)
    }
}

private struct TranscriptBubble: View {
    let message: TranscriptMessage
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorSchemeContrast) private var contrast
    private var isUser: Bool { message.role == "user" }
    private var interrupted: Bool { message.interruption != nil }
    private var live: Bool { !message.isFinal && !interrupted }
    private var shape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(topLeadingRadius: 22, bottomLeadingRadius: isUser ? 22 : 6,
                               bottomTrailingRadius: isUser ? 6 : 22, topTrailingRadius: 22)
    }
    private var foreground: Color {
        if live { return isUser ? VoicePalette.liveBlue : VoicePalette.violet }
        return isUser ? .white : .primary
    }
    private var background: Color {
        if live { return (isUser ? VoicePalette.liveBlue : VoicePalette.violet).opacity(0.10) }
        return isUser ? VoicePalette.blue : VoicePalette.surface
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 0) {
            if isUser { Spacer(minLength: 44) }
            VStack(alignment: isUser ? .trailing : .leading, spacing: 6) {
                Text(message.text)
                    .font(.body).lineSpacing(3)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .foregroundStyle(foreground)
                    .padding(.horizontal, 17).padding(.vertical, 12)
                    .background(background, in: shape)
                    .overlay {
                        shape.strokeBorder(interrupted ? VoicePalette.amber.opacity(0.4) :
                                            live ? foreground.opacity(0.28) : Color.primary.opacity(contrast == .increased ? 0.25 : 0.035),
                                           style: StrokeStyle(lineWidth: 1, dash: live ? [4, 3] : []))
                    }
                    .contextMenu {
                        Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = message.text }
                    }
                HStack(spacing: 5) {
                    Text(isUser ? "You" : "Pipecat")
                    if live {
                        Circle().frame(width: 3, height: 3).accessibilityHidden(true)
                        Text(isUser ? "Transcribing" : "Composing")
                        Image(systemName: isUser ? "waveform" : "ellipsis")
                            .accessibilityHidden(true)
                    }
                }
                .font(.caption2.weight(live ? .medium : .regular))
                .foregroundStyle(live ? foreground : .secondary)
                .padding(.horizontal, 5)
            }
            if !isUser { Spacer(minLength: 44) }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.25), value: message.isFinal)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(isUser ? "You" : "Pipecat"): \(message.text)")
        .accessibilityValue(live ? "\(isUser ? "Transcribing" : "Composing"), not final" : interrupted ? "Reply cut short" : "Final")
        .accessibilityAction(named: "Copy message") { UIPasteboard.general.string = message.text }
    }
}

private struct InterruptionMarker: View {
    let reason: TranscriptInterruption

    var body: some View {
        HStack(spacing: 10) {
            Rectangle().fill(VoicePalette.amber.opacity(0.20)).frame(height: 1)
            Label(reason.rawValue, systemImage: reason == .user ? "arrow.uturn.backward" : "pause.fill")
                .font(.caption2.weight(.medium))
                .lineLimit(1)
                .foregroundStyle(VoicePalette.amber)
                .padding(.horizontal, 11).padding(.vertical, 7)
                .background(VoicePalette.amber.opacity(0.09), in: Capsule())
                .fixedSize(horizontal: true, vertical: false)
                .layoutPriority(1)
            Rectangle().fill(VoicePalette.amber.opacity(0.20)).frame(height: 1)
        }
        .padding(.horizontal, 16)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(reason.rawValue). Pipecat’s reply was cut short.")
    }
}
