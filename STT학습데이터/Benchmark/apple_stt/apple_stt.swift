// Apple 음성인식(iPhone 받아쓰기·Safari Web Speech API와 같은 엔진)으로 녹음을 전사한다.
//
// 사용법: apple_stt <mode> <input.tsv> <out.jsonl>
//   mode: sf-ondevice  SFSpeechRecognizer, 기기 내 인식
//         sf-server    SFSpeechRecognizer, Apple 서버 인식 (Safari 음성 입력 기본 경로)
//         dictation    SpeechAnalyzer + DictationTranscriber (iOS 26 키보드 받아쓰기 계열)
//         transcriber  SpeechAnalyzer + SpeechTranscriber (iOS 26 신규 온디바이스 모델)
//   input.tsv: id \t wav 경로 \t 힌트 용어(| 구분, 없으면 빈칸)
import AVFoundation
import Foundation
import Speech

let locale = Locale(identifier: "ko-KR")

struct Item { let id: String; let url: URL; let hints: [String] }

func loadItems(_ path: String) throws -> [Item] {
    try String(contentsOfFile: path, encoding: .utf8).split(separator: "\n").map { line in
        let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        return Item(id: f[0], url: URL(fileURLWithPath: f[1]),
                    hints: f.count > 2 ? f[2].split(separator: "|").map(String.init) : [])
    }
}

func recognizeSF(_ rec: SFSpeechRecognizer, _ item: Item, onDevice: Bool) async throws -> String {
    let req = SFSpeechURLRecognitionRequest(url: item.url)
    req.requiresOnDeviceRecognition = onDevice
    req.shouldReportPartialResults = false
    req.addsPunctuation = true
    req.contextualStrings = item.hints
    return try await withCheckedThrowingContinuation { cont in
        var finished = false
        rec.recognitionTask(with: req) { result, error in
            if finished { return }
            if let result, result.isFinal {
                finished = true
                cont.resume(returning: result.bestTranscription.formattedString)
            } else if let error {
                finished = true
                // 무음 등으로 결과가 없으면 빈 문자열로 처리
                if (error as NSError).code == 1110 { cont.resume(returning: "") } else { cont.resume(throwing: error) }
            }
        }
    }
}

func analyzerModule(_ mode: String) -> any SpeechModule {
    if mode == "dictation" {
        return DictationTranscriber(locale: locale, preset: .longDictation)
    }
    return SpeechTranscriber(locale: locale, preset: .transcription)
}

func installAssets(_ module: any SpeechModule) async throws {
    if let req = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
        FileHandle.standardError.write("[apple] 음성 모델 에셋 설치 중...\n".data(using: .utf8)!)
        try await req.downloadAndInstall()
    }
}

func recognizeAnalyzer(_ mode: String, _ item: Item) async throws -> String {
    let module = analyzerModule(mode)
    let analyzer = SpeechAnalyzer(modules: [module])
    if !item.hints.isEmpty {
        let ctx = AnalysisContext()
        ctx.contextualStrings[.general] = item.hints
        try await analyzer.setContext(ctx)
    }
    let collect = Task { () -> String in
        var text = ""
        if let m = module as? DictationTranscriber {
            for try await r in m.results where r.isFinal { text += String(r.text.characters) }
        } else if let m = module as? SpeechTranscriber {
            for try await r in m.results where r.isFinal { text += String(r.text.characters) }
        }
        return text
    }
    let file = try AVAudioFile(forReading: item.url)
    try await analyzer.start(inputAudioFile: file, finishAfterFile: true)
    return try await collect.value.trimmingCharacters(in: .whitespacesAndNewlines)
}

@main
struct AppleSTT {
    static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 4 else {
            print("usage: apple_stt <sf-ondevice|sf-server|dictation|transcriber> <input.tsv> <out.jsonl>")
            exit(2)
        }
        let mode = args[1]
        let items = try loadItems(args[2])
        let outURL = URL(fileURLWithPath: args[3])
        FileManager.default.createFile(atPath: outURL.path, contents: nil)
        let out = try FileHandle(forWritingTo: outURL)

        var recognize: (Item) async throws -> String
        if mode.hasPrefix("sf-") {
            let status = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) } }
            guard status == .authorized else { print("음성 인식 권한 없음: \(status.rawValue)"); exit(1) }
            guard let rec = SFSpeechRecognizer(locale: locale), rec.isAvailable else { print("ko-KR 인식기 없음"); exit(1) }
            let onDevice = mode == "sf-ondevice"
            if onDevice && !rec.supportsOnDeviceRecognition { print("ko-KR 기기 내 인식 미지원"); exit(1) }
            recognize = { try await recognizeSF(rec, $0, onDevice: onDevice) }
        } else {
            let supported = mode == "dictation" ? await DictationTranscriber.supportedLocales : await SpeechTranscriber.supportedLocales
            guard supported.contains(where: { $0.identifier(.bcp47) == "ko-KR" }) else {
                print("\(mode): ko-KR 미지원. 지원 로캘 \(supported.count)개"); exit(1)
            }
            try await installAssets(analyzerModule(mode))
            recognize = { try await recognizeAnalyzer(mode, $0) }
        }

        _ = try await recognize(items[0])  // 워밍업
        for (i, item) in items.enumerated() {
            let t = Date()
            let hyp = try await recognize(item)
            let dt = Date().timeIntervalSince(t)
            let row: [String: Any] = ["id": item.id, "hyp": hyp, "proc_sec": (dt * 10000).rounded() / 10000]
            out.write(try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]))
            out.write("\n".data(using: .utf8)!)
            if (i + 1) % 30 == 0 { print("[apple \(mode)] \(i + 1)/\(items.count)") }
        }
        try out.close()
    }
}
