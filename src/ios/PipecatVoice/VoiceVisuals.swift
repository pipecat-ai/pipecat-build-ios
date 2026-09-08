import SwiftUI

enum VoicePalette {
    static let blue = Color(red: 0.08, green: 0.36, blue: 0.87)
    static let violet = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 0.76, green: 0.67, blue: 1, alpha: 1)
        : UIColor(red: 0.40, green: 0.27, blue: 0.72, alpha: 1) })
    static let green = Color(red: 0.10, green: 0.57, blue: 0.35)
    static let red = Color(red: 0.85, green: 0.20, blue: 0.27)
    static let amber = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 1, green: 0.75, blue: 0.37, alpha: 1)
        : UIColor(red: 0.57, green: 0.32, blue: 0.06, alpha: 1) })
    static let liveBlue = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 0.54, green: 0.75, blue: 1, alpha: 1)
        : UIColor(red: 0.10, green: 0.34, blue: 0.67, alpha: 1) })
    static let surface = Color(uiColor: .secondarySystemGroupedBackground)
}

/// Bot playback owns the envelope and clock; microphone input is never used.
struct BotVoiceOrb: View {
    let level: Double
    let speaking: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.colorScheme) private var colorScheme
    @State private var motion = OrbMotion()

    private enum MotionMode: Hashable { case speaking, settling, suspended }
    private var mode: MotionMode {
        if reduceMotion || scenePhase != .active { return .suspended }
        return speaking ? .speaking : .settling
    }
    private var energy: Double {
        speaking && level.isFinite ? min(1, max(0, level)) : 0
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60, paused: !motion.running)) { timeline in
            let sample = motion.sample(at: timeline.date)
            GeometryReader { geometry in
                Rectangle().fill(ShaderLibrary.botVoiceWisps(
                    .float2(geometry.size),
                    .float(sample.phase),
                    .float(reduceMotion ? 0 : sample.energy),
                    .float(colorScheme == .dark ? 1.0 : 0.0)))
            }
        }
        .task(id: mode) {
            switch mode {
            case .speaking:
                motion.retarget(energy, speaking: true, running: true)
            case .settling:
                guard motion.running else { return }
                motion.retarget(0, speaking: false, running: true)
                do { try await Task.sleep(for: .seconds(1.2)) }
                catch { return } // A new reply cancels the previous release.
                motion.retarget(0, speaking: false, running: false)
            case .suspended:
                motion.retarget(0, speaking: false, running: false)
            }
        }
        .onChange(of: energy) { _, value in
            if mode == .speaking { motion.retarget(value, speaking: true, running: true) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Pipecat voice")
        .accessibilityValue(speaking ? "Speaking" : "Quiet")
    }
}

/// An analytic attack/release envelope avoids per-frame state writes. Integrating
/// its speed preserves the wisps' position across audio chunks, silence, and resume.
private struct OrbMotion {
    struct Sample {
        let phase: Double
        let energy: Double
        let activity: Double
    }
    private var anchor = Date()
    private var phase = 1.8
    private var initialEnergy = 0.0
    private var target = 0.0
    private var response = 0.045
    private var initialActivity = 0.0
    private var targetActivity = 0.0
    private(set) var running = false

    func sample(at date: Date) -> Sample {
        let elapsed = running ? max(0, date.timeIntervalSince(anchor)) : 0
        let decay = exp(-elapsed / response)
        let energy = target + (initialEnergy - target) * decay
        let integral = target * elapsed + (initialEnergy - target) * response * (1 - decay)
        let motionDecay = exp(-elapsed / 0.28)
        let activity = targetActivity + (initialActivity - targetActivity) * motionDecay
        let motionIntegral = targetActivity * elapsed
            + (initialActivity - targetActivity) * 0.28 * (1 - motionDecay)
        return Sample(phase: phase + motionIntegral * 0.9 + integral * 2.8,
                      energy: energy, activity: activity)
    }

    mutating func retarget(_ energy: Double, speaking: Bool, running: Bool, at date: Date = Date()) {
        let current = sample(at: date)
        phase = current.phase
        initialEnergy = running ? current.energy : 0
        target = energy
        initialActivity = running ? current.activity : 0
        targetActivity = speaking ? 1 : 0
        response = energy > current.energy ? 0.045 : 0.18
        anchor = date
        self.running = running
    }
}

struct MicrophoneControl: View {
    let level: Double
    let muted: Bool
    let connecting: Bool
    let active: Bool
    let action: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var color: Color { muted ? VoicePalette.red : VoicePalette.green }

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().fill(color.gradient)
                Circle().strokeBorder(.white.opacity(0.24), lineWidth: 1)
                if connecting {
                    ProgressView().tint(.white).controlSize(.regular).scaleEffect(1.15)
                } else {
                    VStack(spacing: 5) {
                        Image(systemName: muted ? "mic.slash.fill" : "mic.fill")
                            .font(.system(size: 21, weight: .semibold))
                        HStack(alignment: .center, spacing: 2.5) {
                            ForEach(0..<9) { index in
                                Capsule().frame(width: 2.5, height: barHeight(index))
                            }
                        }.frame(height: 13)
                    }.foregroundStyle(.white)
                }
            }.frame(width: 72, height: 72)
                .shadow(color: color.opacity(0.24), radius: 10, y: 4)
                .contentShape(Circle())
        }
        .buttonStyle(VoicePressStyle())
        .disabled(connecting)
        .accessibilityIdentifier(active ? "microphoneControl" : "primaryVoiceControl")
        .accessibilityLabel(connecting ? "Connecting" : !active ? "Start conversation" : muted ? "Unmute microphone" : "Mute microphone")
        .accessibilityValue(connecting ? "Please wait" : !active ? "Ready" : muted ? "Muted" : "Microphone on")
        .accessibilityHint(active && !connecting ? "Double tap to \(muted ? "resume" : "pause") your microphone" : "")
        .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: level)
        .animation(reduceMotion ? nil : .smooth(duration: 0.2), value: muted)
    }

    private func barHeight(_ index: Int) -> CGFloat {
        guard active, !muted, level.isFinite else { return 2.5 }
        let envelope = sin(Double(index + 1) / 10 * .pi)
        let energy = min(1, max(0, level))
        // The bars remain still in silence; there is no synthetic speech signal.
        let variation = 0.65 + 0.35 * abs(sin(Double(index) * 2.3 + energy * 5))
        return 2.5 + envelope * energy * variation * 11
    }
}

struct VoicePressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.94 : 1)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(reduceMotion ? nil : .spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }
}

struct ThinkingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        HStack(spacing: 8) {
            TimelineView(.animation(minimumInterval: 1.0 / 12, paused: reduceMotion || scenePhase != .active)) { context in
                HStack(spacing: 4) {
                    ForEach(0..<3) { index in
                        let wave = reduceMotion ? 0.5 : (sin(context.date.timeIntervalSinceReferenceDate * 4 - Double(index) * 0.9) + 1) / 2
                        Circle().fill(VoicePalette.violet.opacity(0.35 + wave * 0.65))
                            .frame(width: 6, height: 6)
                            .offset(y: reduceMotion ? 0 : -wave * 3)
                    }
                }.frame(height: 12)
            }.frame(width: 28)
            Text("Thinking").font(.caption).foregroundStyle(VoicePalette.violet)
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .background(VoicePalette.violet.opacity(0.09), in: RoundedRectangle(cornerRadius: 20))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Pipecat is thinking")
    }
}
