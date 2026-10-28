import CoreImage
import Foundation

// QR codes for the terminal, so a phone camera can open the pairing link. CoreImage draws the
// symbol; `modules` reads it back as a boolean grid and `terminalLines` prints it with half-block
// characters on a light background (two modules per character row), which iPhone cameras read
// reliably even from a dark terminal.

enum QRCode {
    static func modules(_ text: String) throws -> [[Bool]] {
        guard let filter = CIFilter(name: "CIQRCodeGenerator") else { throw Failure(description: "CIQRCodeGenerator unavailable") }
        filter.setValue(Data(text.utf8), forKey: "inputMessage")
        filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let image = filter.outputImage else { throw Failure(description: "QR generation failed") }
        let width = Int(image.extent.width), height = Int(image.extent.height)
        var pixels = [UInt8](repeating: 0, count: width * height)
        let context = CIContext(options: [.useSoftwareRenderer: true])
        context.render(image, toBitmap: &pixels, rowBytes: width, bounds: image.extent,
                       format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
        // CoreImage's QR output is the symbol plus a one-module quiet zone on each side.
        return (0..<height).map { y in (0..<width).map { x in pixels[y * width + x] < 128 } }
    }

    static func terminalLines(_ grid: [[Bool]], quiet: Int = 2) -> [String] {
        let size = grid.count
        let span = size + quiet * 2
        func dark(_ x: Int, _ y: Int) -> Bool {
            let gx = x - quiet, gy = y - quiet
            return gx >= 0 && gy >= 0 && gx < size && gy < size && grid[gy][gx]
        }
        var lines: [String] = []
        var y = 0
        while y < span {
            var line = ""
            for x in 0..<span {
                let top = dark(x, y), bottom = y + 1 < span ? dark(x, y + 1) : false
                switch (top, bottom) {
                case (false, false): line += "█"
                case (false, true): line += "▀"
                case (true, false): line += "▄"
                case (true, true): line += " "
                }
            }
            lines.append(line)
            y += 2
        }
        return lines
    }

    static func print(_ text: String) throws {
        for line in terminalLines(try modules(text)) { Swift.print(line) }
    }
}
