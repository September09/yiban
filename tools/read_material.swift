import Foundation
import AppKit
import Vision
import PDFKit
import ImageIO
enum ReadError: Error { case invalid, tooLarge, encrypted }
func recognize(_ image: CGImage) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(cgImage: image).perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}
do {
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    var pages: [[String: Any]] = []
    if CommandLine.arguments[2] == ".pdf" {
        guard let doc = PDFDocument(url: url) else { throw ReadError.invalid }
        if doc.isLocked { throw ReadError.encrypted }
        if doc.pageCount > 20 { throw ReadError.tooLarge }
        for i in 0..<doc.pageCount {
            guard let page = doc.page(at: i) else { throw ReadError.invalid }
            var text = page.string ?? ""
            var ocr = false
            if text.trimmingCharacters(in: .whitespacesAndNewlines).count < 10 {
                let size = page.bounds(for: .mediaBox).size
                guard size.width > 0 && size.height > 0 else { throw ReadError.invalid }
                let scale = min(2.5, 2400 / max(size.width, size.height))
                let thumb = page.thumbnail(of: NSSize(width: size.width * scale, height: size.height * scale), for: .mediaBox)
                guard let cg = thumb.cgImage(forProposedRect: nil, context: nil, hints: nil) else { throw ReadError.invalid }
                text = try recognize(cg); ocr = true
            }
            pages.append(["page": i + 1, "text": text, "ocr": ocr])
        }
    } else {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = props[kCGImagePropertyPixelWidth] as? Int,
              let height = props[kCGImagePropertyPixelHeight] as? Int else { throw ReadError.invalid }
        if width > 20000 || height > 20000 || width * height > 40000000 { throw ReadError.tooLarge }
        let options = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                       kCGImageSourceCreateThumbnailWithTransform: true,
                       kCGImageSourceThumbnailMaxPixelSize: 4000] as CFDictionary
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options) else { throw ReadError.invalid }
        pages.append(["page": 1, "text": try recognize(image), "ocr": true])
    }
    let data = try JSONSerialization.data(withJSONObject: ["pages": pages])
    FileHandle.standardOutput.write(data)
} catch {
    let data = try! JSONSerialization.data(withJSONObject: ["error": "文件无法读取、已加密或超过限制（PDF 20 页，图片 4000 万像素）。"])
    FileHandle.standardOutput.write(data)
    exit(1)
}
