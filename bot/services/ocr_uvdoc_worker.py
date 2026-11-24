"""عامل فكّ التواء الصفحات (UVDoc عبر ONNX Runtime) — عملية فرعية معزولة.

يُشغَّل هكذا (لا يُستورَد من البوت أبدًا — انظر `ocr.py` لقصة العزل):

    python -m bot.services.ocr_uvdoc_worker <model.onnx>

البروتوكول سطرًا بسطر على stdin/stdout:
  * بعد تحميل النموذج يطبع `READY`.
  * كل سطر وارد = مسار صورة؛ الرد `OK <output>` حيث الإخراج هو
    `<input>.dewarped.png`، أو `ERR <reason>` عند فشل صفحة بعينها
    (العامل يبقى حيًّا بعدها). EOF على stdin = خروج نظيف.

لماذا ONNX لا Paddle؟ النموذج نفسه UVDoc الذي أعطى مكسبه الوحيد
داخل PaddleOCR، لكن بحجم ٣٢MB وزمن إقلاع ثوانٍ بدل ~٦٠٠MB — انظر
قسم UVDoc في `ocr.py` للقياسات. الوصفة مطابقة لأصل PaddleX:
الصورة كاملة الحجم ÷٢٥٥ بلا أي تصغير (التصغير قِيس فانهار على
صفحة حقيقية: ٦٢٩ بدل ٢٣٤٧ حرف)، والمخرجات ×٢٥٥ قصّ لقيم ودية.
"""
from __future__ import annotations

import sys

import numpy as np
import onnxruntime as ort
from PIL import Image


def _dewarp(sess: ort.InferenceSession, image: Image.Image) -> Image.Image:
    gray = np.asarray(image.convert("L"))
    x = (np.stack([gray] * 3, axis=-1).astype(np.float32) / 255.0)
    x = x.transpose(2, 0, 1)[None]
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    arr = np.clip(out[0].transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr[..., 0], "L")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ocr_uvdoc_worker <model.onnx>", file=sys.stderr)
        return 2
    opts = ort.SessionOptions()
    # بلا ساحة ذاكرة: تُبقي الذروة محدودة ومستقرة عبر الصفحات (قِيست
    # ٨٢٥MB ثابتة على لقطات ٤٠٠٠px بدل نمو مطّرد).
    opts.enable_cpu_mem_arena = False
    sess = ort.InferenceSession(
        sys.argv[1], sess_options=opts, providers=["CPUExecutionProvider"])
    print("READY", flush=True)
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            with Image.open(path) as image:
                result = _dewarp(sess, image)
            out_path = path + ".dewarped.png"
            result.save(out_path)
            print(f"OK {out_path}", flush=True)
        except Exception as exc:  # صفحة فاسدة لا تقتل العامل
            print(f"ERR {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
