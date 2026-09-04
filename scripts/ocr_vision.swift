#!/usr/bin/env swift
// 文件名：ocr_vision.swift
// 作用：基于 macOS 系统自带 Vision 框架的本地 OCR 工具（无需安装任何依赖）
// 为什么用这个方案：PaddleOCR 不支持 Python 3.14，tesseract 未安装；而 macOS 自带的 Vision
//      框架中英文识别效果很好，零依赖、不占磁盘，直接用系统能力。
// 用法：
//   swift ocr_vision.swift <图片1> [图片2 ...]
//   swift ocr_vision.swift -s <图片>    # 只输出识别到的文字内容（不含分隔线，便于脚本调用）
// 输出：识别到的文字按从上到下、从左到右的顺序打印。

import Vision
import AppKit
import Foundation

// 解析命令行参数
let args = CommandLine.arguments
// 默认输出模式：带文件名分隔线；-s 表示静默模式（只输出文字）
var silentMode = false
var imagePaths: [String] = []

var i = 1
while i < args.count {
    let arg = args[i]
    if arg == "-s" {
        silentMode = true
    } else {
        imagePaths.append(arg)
    }
    i += 1
}

// 没有传入图片时打印用法并退出
guard !imagePaths.isEmpty else {
    print("用法: swift ocr_vision.swift [-s] <图片1> [图片2 ...]")
    exit(1)
}

// 对单张图片执行 OCR，返回识别出的文本行数组
func ocrImage(_ path: String) -> [String] {
    // 加载图片为 NSImage，再转成 CGImage（Vision 框架需要 CGImage）
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("无法加载图片: \(path)")
        return []
    }

    var lines: [String] = []
    // 创建文本识别请求，识别结果在回调里收集
    let request = VNRecognizeTextRequest { req, _ in
        guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
        // 按坐标排序：先按 y（从上到下），同行再按 x（从左到右），保持正常阅读顺序
        let sorted = obs.sorted { a, b in
            let ay = a.boundingBox.midY
            let by = b.boundingBox.midY
            if abs(ay - by) > 0.02 { return ay > by }
            return a.boundingBox.minX < b.boundingBox.minX
        }
        for o in sorted {
            if let t = o.topCandidates(1).first {
                lines.append(t.string)
            }
        }
    }
    // 使用高精度识别，支持中文简体 + 英文（视频字幕、图片文字都能认）
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false

    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do {
        try handler.perform([request])
    } catch {
        print("OCR 失败: \(error)")
    }
    return lines
}

// 逐个处理图片：静默模式只打印文字；普通模式打印文件名分隔线便于区分
for path in imagePaths {
    if !silentMode {
        print("===== \(path) =====")
    }
    let lines = ocrImage(path)
    for line in lines {
        print(line)
    }
}
