// Muesli의 후처리 사전 코드(CustomWordMatcher.swift, MIT, Muesli-HQ/muesli@0f0e8948dfb2)를 고치지 않고 그대로 불러
// 전사 결과에 적용한다.
// 입력(stdin, 한 줄에 JSON 하나): {"id": ..., "text": 전사, "terms": [사전 단어...], "threshold": 0.85}
// 출력(stdout, 한 줄에 JSON 하나): {"id": ..., "text": 교정된 전사}
import Foundation

while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let text = obj["text"] as? String,
          let terms = obj["terms"] as? [String],
          let threshold = obj["threshold"] as? Double else { continue }
    let words = terms.map { CustomWord(word: $0, replacement: nil, matchingThreshold: threshold) }
    let out: [String: Any] = ["id": obj["id"] ?? "", "text": CustomWordMatcher.apply(text: text, customWords: words)]
    let json = try! JSONSerialization.data(withJSONObject: out)
    print(String(data: json, encoding: .utf8)!)
}
