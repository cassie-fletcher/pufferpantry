// VisionOCR.swift — a thin command-line shim over Apple's Vision text recognizer.
//
// WHY THIS EXISTS
// ---------------
// The Python side of PufferPantry has no pyobjc/ocrmac, and we are not adding
// them. This binary is the only bridge to VNRecognizeTextRequest. It does the
// smallest possible job: run recognition and dump the raw results as JSON.
// All interpretation (coordinate flipping, reading order, parsing) happens in
// Python, where it can be read, tested and debugged.
//
// CONTRACT
// --------
//   vision_ocr <image_path>      # a JSON options object is read from stdin
//   vision_ocr --version         # one JSON line, does not read stdin
//
// *** stdout carries EXACTLY ONE JSON object and nothing else. ***
// A stray `print(...)` here goes to stdout and will break json.loads() on the
// Python side with a confusing "Extra data" error. Every diagnostic, warning
// or debug message MUST go to stderr (use `warn(...)` below). If you are
// tempted to add a print, don't.
//
// On failure: non-zero exit status, and a JSON error object on stderr.
//
// COORDINATES
// -----------
// Bounding boxes are emitted EXACTLY as Vision reports them: normalized to
// [0, 1], origin at the BOTTOM-LEFT of the image, y increasing UPWARD. We do
// not flip them here. The top-level "coordinate_space" field records this so
// the Python side can assert on it instead of trusting a comment.
//
// NETWORK
// -------
// VNRecognizeTextRequest is fully on-device. This binary makes no network
// calls and must never be given any.

import CoreGraphics
import Foundation
import ImageIO
import Vision

// MARK: - Version

let TOOL_NAME = "vision_ocr"
let TOOL_VERSION = "1.0.0"

// MARK: - Defaults
//
// These are the values used when the stdin options object omits a key.

let DEFAULT_RECOGNITION_LEVEL = "accurate"
let DEFAULT_USES_LANGUAGE_CORRECTION = true
let DEFAULT_RECOGNITION_LANGUAGES = ["en-US"]

// minimumTextHeight is a FRACTION OF IMAGE HEIGHT, not pixels. Text shorter
// than this is not looked for, so setting it too high silently drops lines and
// makes the recognizer look broken.
//
// MEASURED, not assumed: on macOS 14.6 with revision 3, `--version` reports
// framework_default_minimum_text_height = 0.0 (NOT the 1/32 that older docs
// describe). On the 1568px Sunday Chicken page, 0.0 / 0.004 / 0.008 / 0.016 /
// 0.03125 all yield 85 observations and 3225 characters; 0.05 and above yield
// only 69 observations and 2585 characters. So 0.008 is lossless here while
// still filtering sub-13px specks. Re-measure before trusting it elsewhere.
let DEFAULT_MINIMUM_TEXT_HEIGHT: Float = 0.008

let DEFAULT_MAX_CANDIDATES = 3

// Revision 3 is the current text-recognition model on macOS 13+. If the OS
// does not support it we fall back to the framework default and say so on
// stderr rather than crashing at perform() time.
let DEFAULT_REVISION = 3

// MARK: - stderr helpers

func writeStderr(_ s: String) {
    FileHandle.standardError.write(Data(s.utf8))
}

/// Human-readable diagnostic. Always stderr, never stdout.
func warn(_ message: String) {
    writeStderr("[\(TOOL_NAME)] \(message)\n")
}

struct ErrorOut: Encodable {
    let error: String
    let detail: String
}

/// Emit a JSON error object on stderr and exit non-zero. Never returns.
func fail(_ error: String, _ detail: String, code: Int32 = 1) -> Never {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    if let data = try? encoder.encode(ErrorOut(error: error, detail: detail)) {
        FileHandle.standardError.write(data)
        writeStderr("\n")
    } else {
        writeStderr("{\"error\":\"\(error)\",\"detail\":\"encoding failed\"}\n")
    }
    exit(code)
}

// MARK: - Options (decoded from the stdin JSON)

/// Every field is optional; a missing field means "use the default above".
struct Options: Decodable {
    var recognitionLevel: String?
    var usesLanguageCorrection: Bool?
    var recognitionLanguages: [String]?
    var minimumTextHeight: Float?
    var maxCandidates: Int?
    var revision: Int?
}

/// The options actually applied, echoed back so the caller never has to guess.
struct EffectiveOptions: Encodable {
    let recognitionLevel: String
    let usesLanguageCorrection: Bool
    let recognitionLanguages: [String]
    let minimumTextHeight: Float
    let maxCandidates: Int
    let revision: Int
}

// MARK: - Output shape

struct BBox: Encodable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct Candidate: Encodable {
    let string: String
    let confidence: Double
}

struct Observation: Encodable {
    /// Vision-native: normalized, bottom-left origin, y increasing upward.
    let bbox: BBox
    /// Observation-level confidence. Vision gives no per-word confidence.
    let confidence: Double
    /// Up to maxCandidates alternative readings, best first.
    let candidates: [Candidate]
}

struct ImageInfo: Encodable {
    /// Dimensions as the recognizer saw them, i.e. after EXIF orientation is
    /// applied. Normalized bboxes are relative to THESE. Use these in Python.
    let width: Int
    let height: Int
    /// Raw stored pixel dimensions, before orientation. Equal to the above
    /// unless orientation is 5-8 (a 90-degree rotation).
    let pixel_width: Int
    let pixel_height: Int
    /// EXIF orientation tag (1 = up / no transform).
    let orientation: UInt32
}

struct Output: Encodable {
    let tool: String
    let version: String
    let coordinate_space: String
    let image: ImageInfo
    let options: EffectiveOptions
    let observation_count: Int
    let observations: [Observation]
}

struct VersionOut: Encodable {
    let tool: String
    let version: String
    let coordinate_space: String
    let supported_revisions: [Int]
    let supported_languages: [String]
    /// What Vision uses if minimumTextHeight is never assigned. Reported so the
    /// "is our default lower than the framework's?" question is measurable.
    let framework_default_minimum_text_height: Float
    let defaults: EffectiveOptions
}

let COORDINATE_SPACE = "vision_normalized_bottom_left_y_up"

func defaultOptions() -> EffectiveOptions {
    EffectiveOptions(
        recognitionLevel: DEFAULT_RECOGNITION_LEVEL,
        usesLanguageCorrection: DEFAULT_USES_LANGUAGE_CORRECTION,
        recognitionLanguages: DEFAULT_RECOGNITION_LANGUAGES,
        minimumTextHeight: DEFAULT_MINIMUM_TEXT_HEIGHT,
        maxCandidates: DEFAULT_MAX_CANDIDATES,
        revision: DEFAULT_REVISION
    )
}

/// Write the single stdout JSON object. Called exactly once per run.
func emit<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    do {
        let data = try encoder.encode(value)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        fail("json_encode_failed", "\(error)")
    }
}

// MARK: - --version

func supportedRevisions() -> [Int] {
    VNRecognizeTextRequest.supportedRevisions.sorted()
}

func supportedLanguages(revision: Int) -> [String] {
    let request = VNRecognizeTextRequest()
    if VNRecognizeTextRequest.supportedRevisions.contains(revision) {
        request.revision = revision
    }
    return (try? request.supportedRecognitionLanguages()) ?? []
}

func runVersion() -> Never {
    emit(
        VersionOut(
            tool: TOOL_NAME,
            version: TOOL_VERSION,
            coordinate_space: COORDINATE_SPACE,
            supported_revisions: supportedRevisions(),
            supported_languages: supportedLanguages(revision: DEFAULT_REVISION),
            framework_default_minimum_text_height: VNRecognizeTextRequest().minimumTextHeight,
            defaults: defaultOptions()
        ))
    exit(0)
}

// MARK: - Image loading

/// Load a CGImage plus its EXIF orientation. Vision needs the orientation
/// separately because CGImageSourceCreateImageAtIndex ignores it.
func loadImage(path: String) -> (CGImage, CGImagePropertyOrientation) {
    guard FileManager.default.fileExists(atPath: path) else {
        fail("image_not_found", path)
    }
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
        fail("image_unreadable", "CGImageSourceCreateWithURL failed for \(path)")
    }
    guard let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("image_decode_failed", "CGImageSourceCreateImageAtIndex failed for \(path)")
    }

    var orientation = CGImagePropertyOrientation.up
    if let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
        let raw = props[kCGImagePropertyOrientation] as? UInt32,
        let parsed = CGImagePropertyOrientation(rawValue: raw)
    {
        orientation = parsed
    }
    return (image, orientation)
}

/// Orientations 5-8 rotate by 90 degrees, so the recognizer sees width and
/// height swapped relative to the stored pixel buffer.
func orientationSwapsAxes(_ o: CGImagePropertyOrientation) -> Bool {
    switch o {
    case .left, .leftMirrored, .right, .rightMirrored: return true
    default: return false
    }
}

// MARK: - Main

let args = CommandLine.arguments

if args.count == 2 && args[1] == "--version" {
    runVersion()
}

guard args.count == 2, !args[1].hasPrefix("--") else {
    fail(
        "usage",
        "usage: \(TOOL_NAME) <image_path>   (JSON options object on stdin) "
            + "| \(TOOL_NAME) --version", code: 2)
}

let imagePath = args[1]

// Read the options object from stdin. Empty stdin means "all defaults".
// NOTE: this blocks if stdin is a terminal. Run manually with `< /dev/null`.
let stdinData = FileHandle.standardInput.readDataToEndOfFile()
var opts = Options()
let trimmed = String(data: stdinData, encoding: .utf8)?.trimmingCharacters(
    in: .whitespacesAndNewlines) ?? ""
if !trimmed.isEmpty {
    do {
        opts = try JSONDecoder().decode(Options.self, from: Data(trimmed.utf8))
    } catch {
        fail("bad_options_json", "could not decode options from stdin: \(error)", code: 2)
    }
}

// Resolve each option against its default.
let levelName = opts.recognitionLevel ?? DEFAULT_RECOGNITION_LEVEL
let level: VNRequestTextRecognitionLevel
switch levelName.lowercased() {
case "accurate": level = .accurate
case "fast": level = .fast
default:
    fail("bad_option", "recognitionLevel must be \"accurate\" or \"fast\", got \"\(levelName)\"", code: 2)
}

let useCorrection = opts.usesLanguageCorrection ?? DEFAULT_USES_LANGUAGE_CORRECTION
let languages = opts.recognitionLanguages ?? DEFAULT_RECOGNITION_LANGUAGES
let minTextHeight = opts.minimumTextHeight ?? DEFAULT_MINIMUM_TEXT_HEIGHT
let maxCandidates = max(1, opts.maxCandidates ?? DEFAULT_MAX_CANDIDATES)

guard minTextHeight >= 0.0 && minTextHeight <= 1.0 else {
    fail(
        "bad_option",
        "minimumTextHeight is a fraction of image height and must be in [0, 1], "
            + "got \(minTextHeight)", code: 2)
}

let (cgImage, orientation) = loadImage(path: imagePath)

let request = VNRecognizeTextRequest()
request.recognitionLevel = level
request.usesLanguageCorrection = useCorrection
request.recognitionLanguages = languages
request.minimumTextHeight = minTextHeight

var appliedRevision = request.revision
let wantedRevision = opts.revision ?? DEFAULT_REVISION
if VNRecognizeTextRequest.supportedRevisions.contains(wantedRevision) {
    request.revision = wantedRevision
    appliedRevision = wantedRevision
} else {
    warn(
        "revision \(wantedRevision) is not supported here "
            + "(supported: \(supportedRevisions())); using framework default \(appliedRevision)")
}

let handler = VNImageRequestHandler(cgImage: cgImage, orientation: orientation, options: [:])

// try handler.perform([...]) is SYNCHRONOUS. Results are available on the
// request as soon as it returns — no completion handler, no semaphore.
do {
    try handler.perform([request])
} catch {
    fail("recognition_failed", "\(error)")
}

let results = request.results ?? []
var observations: [Observation] = []
observations.reserveCapacity(results.count)

for obs in results {
    // Emit the box unmodified. The flip to top-left origin is Python's job.
    let box = obs.boundingBox
    let candidates = obs.topCandidates(maxCandidates).map {
        Candidate(string: $0.string, confidence: Double($0.confidence))
    }
    observations.append(
        Observation(
            bbox: BBox(
                x: Double(box.origin.x), y: Double(box.origin.y),
                width: Double(box.size.width), height: Double(box.size.height)),
            confidence: Double(obs.confidence),
            candidates: candidates
        ))
}

let swaps = orientationSwapsAxes(orientation)
let imageInfo = ImageInfo(
    width: swaps ? cgImage.height : cgImage.width,
    height: swaps ? cgImage.width : cgImage.height,
    pixel_width: cgImage.width,
    pixel_height: cgImage.height,
    orientation: orientation.rawValue
)

if observations.isEmpty {
    warn(
        "0 observations. If the page clearly has text, minimumTextHeight "
            + "(\(minTextHeight)) may be too large for this image.")
}

emit(
    Output(
        tool: TOOL_NAME,
        version: TOOL_VERSION,
        coordinate_space: COORDINATE_SPACE,
        image: imageInfo,
        options: EffectiveOptions(
            recognitionLevel: levelName.lowercased(),
            usesLanguageCorrection: useCorrection,
            recognitionLanguages: languages,
            minimumTextHeight: minTextHeight,
            maxCandidates: maxCandidates,
            revision: appliedRevision
        ),
        observation_count: observations.count,
        observations: observations
    ))
