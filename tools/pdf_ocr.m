// 用 macOS Vision 对 PDF 逐页 OCR（中文简体），输出带页码标记的纯文本。
// 编译: clang -fobjc-arc -O2 -framework Foundation -framework PDFKit -framework Vision \
//            -framework CoreGraphics -framework AppKit pdf_ocr.m -o pdf_ocr
// 用法: ./pdf_ocr <in.pdf> <out.txt> [起始页] [结束页]

#import <Foundation/Foundation.h>
#import <PDFKit/PDFKit.h>
#import <Vision/Vision.h>
#import <CoreGraphics/CoreGraphics.h>
#import <AppKit/AppKit.h>

static NSString *ocrCGImage(CGImageRef cg) {
    VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
    req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    req.recognitionLanguages = @[ @"zh-Hans", @"en-US" ];
    req.usesLanguageCorrection = YES;
    VNImageRequestHandler *h = [[VNImageRequestHandler alloc] initWithCGImage:cg options:@{}];
    NSError *err = nil;
    [h performRequests:@[ req ] error:&err];
    if (err || !req.results) return @"";

    NSArray *obs = [req.results sortedArrayUsingComparator:^NSComparisonResult(VNRecognizedTextObservation *a, VNRecognizedTextObservation *b) {
        CGRect ra = a.boundingBox, rb = b.boundingBox;
        if (fabs(CGRectGetMidY(ra) - CGRectGetMidY(rb)) > 0.008) {
            return CGRectGetMidY(ra) > CGRectGetMidY(rb) ? NSOrderedAscending : NSOrderedDescending;
        }
        return CGRectGetMinX(ra) < CGRectGetMinX(rb) ? NSOrderedAscending : NSOrderedDescending;
    }];

    NSMutableArray<NSString *> *lines = [NSMutableArray array];
    NSMutableString *cur = [NSMutableString string];
    CGFloat lastY = -1;
    for (VNRecognizedTextObservation *o in obs) {
        VNRecognizedText *t = [[o topCandidates:1] firstObject];
        if (!t) continue;
        CGFloat y = CGRectGetMidY(o.boundingBox);
        if (lastY < 0 || fabs(y - lastY) < 0.008) {
            [cur appendString:t.string];
        } else {
            if (cur.length) [lines addObject:cur];
            cur = [NSMutableString stringWithString:t.string];
        }
        lastY = y;
    }
    if (cur.length) [lines addObject:cur];
    return [lines componentsJoinedByString:@"\n"];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 3) {
            fprintf(stderr, "usage: pdf_ocr <in.pdf> <out.txt> [from] [to]\n");
            return 1;
        }
        NSString *inPath = [NSString stringWithUTF8String:argv[1]];
        NSString *outPath = [NSString stringWithUTF8String:argv[2]];
        NSInteger from = argc > 3 ? atoi(argv[3]) : 1;
        NSInteger to = argc > 4 ? atoi(argv[4]) : 99999;

        PDFDocument *doc = [[PDFDocument alloc] initWithURL:[NSURL fileURLWithPath:inPath]];
        if (!doc) { fprintf(stderr, "cannot open pdf\n"); return 2; }
        NSInteger total = doc.pageCount;
        NSInteger hi = MIN(to, total);

        NSMutableString *out = [NSMutableString string];
        CGFloat scale = 2.0;
        for (NSInteger i = from - 1; i < hi; i++) {
            PDFPage *page = [doc pageAtIndex:i];
            if (!page) continue;
            CGRect box = [page boundsForBox:kPDFDisplayBoxMediaBox];
            size_t w = (size_t)(box.size.width * scale), h = (size_t)(box.size.height * scale);
            if (w == 0 || h == 0) continue;
            CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
            CGContextRef ctx = CGBitmapContextCreate(NULL, w, h, 8, 0, cs,
                                                     kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
            CGColorSpaceRelease(cs);
            if (!ctx) continue;
            CGContextSetRGBFillColor(ctx, 1, 1, 1, 1);
            CGContextFillRect(ctx, CGRectMake(0, 0, w, h));
            CGContextScaleCTM(ctx, scale, scale);
            CGContextTranslateCTM(ctx, -box.origin.x, -box.origin.y);
            [page drawWithBox:kPDFDisplayBoxMediaBox toContext:ctx];
            CGImageRef cg = CGBitmapContextCreateImage(ctx);
            CGContextRelease(ctx);
            if (!cg) continue;
            NSString *txt = ocrCGImage(cg);
            CGImageRelease(cg);
            [out appendFormat:@"\n===== PAGE %ld =====\n%@\n", (long)(i + 1), txt];
            fprintf(stderr, "[ocr] %ld/%ld\n", (long)(i + 1), (long)hi);
        }
        [out writeToFile:outPath atomically:YES encoding:NSUTF8StringEncoding error:nil];
        printf("done %ld pages -> %s\n", (long)(hi - from + 1), argv[2]);
    }
    return 0;
}
