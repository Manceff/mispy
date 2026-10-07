// Records what the Mac is playing — the other people in a video call — through
// ScreenCaptureKit. No virtual audio device to install, no Audio MIDI Setup, no
// reboot: macOS hands us the system mix directly.
//
//   mispy-audio out.wav          record until SIGINT, write a WAV
//   mispy-audio --check          say whether screen recording is permitted
//
// Needs Screen Recording permission (System Settings > Privacy & Security).
// Nothing is captured from the screen: the stream is configured at 2x2 pixels
// and only its audio is read.
import Foundation
import AVFoundation
import ScreenCaptureKit

let args = CommandLine.arguments

func fail(_ message: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data("mispy-audio: \(message)\n".utf8))
    exit(code)
}

@available(macOS 13.0, *)
final class SystemAudio: NSObject, SCStreamOutput, SCStreamDelegate {
    private let url: URL
    private var file: AVAudioFile?
    private let queue = DispatchQueue(label: "mispy.audio.write")
    private var stream: SCStream?
    private(set) var frames: Int64 = 0

    init(url: URL) { self.url = url }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                          onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            fail("no display to attach to", 3)
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true   // never record ourselves
        config.sampleRate = 48_000
        config.channelCount = 2
        config.width = 2                            // the video side is a formality
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.queueDepth = 5

        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try await s.startCapture()
        stream = s
    }

    func stop() async {
        if let s = stream { try? await s.stopCapture() }
        queue.sync { file = nil }                   // closes and finalises the header
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, sb.numSamples > 0 else { return }
        guard let desc = sb.formatDescription,
              let asbd = desc.audioStreamBasicDescription else { return }
        guard let format = AVAudioFormat(standardFormatWithSampleRate: asbd.mSampleRate,
                                         channels: asbd.mChannelsPerFrame) else { return }
        do {
            if file == nil {
                // 16-bit WAV: half the size of float, and whisper wants 16 kHz PCM anyway.
                file = try AVAudioFile(forWriting: url, settings: [
                    AVFormatIDKey: kAudioFormatLinearPCM,
                    AVSampleRateKey: asbd.mSampleRate,
                    AVNumberOfChannelsKey: Int(asbd.mChannelsPerFrame),
                    AVLinearPCMBitDepthKey: 16,
                    AVLinearPCMIsFloatKey: false,
                    AVLinearPCMIsBigEndianKey: false,
                ], commonFormat: .pcmFormatFloat32, interleaved: false)
            }
            try sb.withAudioBufferList { abl, _ in
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                                    bufferListNoCopy: abl.unsafePointer) else { return }
                try file?.write(from: buffer)
                frames += Int64(buffer.frameLength)
            }
        } catch {
            FileHandle.standardError.write(Data("mispy-audio: write failed: \(error)\n".utf8))
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("mispy-audio: stream stopped: \(error)\n".utf8))
        exit(4)
    }
}

guard #available(macOS 13.0, *) else { fail("needs macOS 13 or later", 2) }

if args.contains("--check") {
    // Asking for the content list is the cheapest way to find out whether the
    // permission exists; macOS shows its dialog here if it never has.
    let sem = DispatchSemaphore(value: 0)
    var allowed = false
    SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: false) { content, _ in
        allowed = (content?.displays.isEmpty == false)
        sem.signal()
    }
    _ = sem.wait(timeout: .now() + 30)
    print(allowed ? "ok" : "denied")
    exit(allowed ? 0 : 5)
}

guard args.count > 1, !args[1].hasPrefix("--") else { fail("usage: mispy-audio <out.wav>") }
let out = URL(fileURLWithPath: args[1])
let recorder = SystemAudio(url: out)

let stopping = DispatchSemaphore(value: 0)
var sources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { stopping.signal() }
    src.resume()
    sources.append(src)
}

Task {
    do { try await recorder.start() }
    catch {
        fail("cannot start: \(error.localizedDescription) — grant Screen Recording in "
             + "System Settings > Privacy & Security", 5)
    }
}

DispatchQueue.global().async {
    stopping.wait()
    Task {
        await recorder.stop()
        exit(0)
    }
}
RunLoop.main.run()
