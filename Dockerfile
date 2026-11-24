FROM python:3.12-slim

# صورة إنتاج كاملة: كل ميزات البوت واعتماديات OCR مفعّلة.
ARG INSTALL_OCR=1

WORKDIR /app
COPY requirements-wispbyte.txt .
RUN pip install --no-cache-dir -r requirements-wispbyte.txt

COPY . .

# للبناء مع OCR: docker build --build-arg INSTALL_OCR=1 .
RUN if [ "$INSTALL_OCR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng tesseract-ocr-osd \
        poppler-utils \
      && rm -rf /var/lib/apt/lists/* \
      && true; \
    fi

# نموذج UVDoc غير مثبت افتراضيًا لتقليل حجم الصورة؛ OCR القياسي لا يحتاجه.

ENV PYTHONUNBUFFERED=1
# فحص صحة حقيقي: العملية قد تكون "حيّة" وهي لا تسحب التحديثات (شبكة مقطوعة
# أو توكن مرفوض). نتحقق أن ملف العملية يشير لعملية غير زومبي وأن البوت
# ما زال يستجيب — و`restart: unless-stopped` يتكفّل بالإعادة.
# فحص صحة حقيقي: البوت يكتب data/.heartbeat كل دقيقة (heartbeat_loop).
# لو توقّفت النبضة ٥ دقائق فمعناه أن حلقة الاستقبال عالقة — والخدمة تُعاد.
HEALTHCHECK --interval=2m --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os,sys,time; p='data/.heartbeat'; \
sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 300 else 1)"
# مجلدات يكتب فيها البوت (قاعدة SQLite الافتراضية + نسخ احتياطية).
# على Railway نظام الملفات مؤقت: استخدم DATABASE_URL خارجيًا (Supabase)
# أو ركّب Volume على /app/data — انظر docs/OPERATIONS.md § Railway.
RUN mkdir -p data backups

# نقطة الدخول الموحّدة: تتحقّق من الحاجيات، تنظّف المخلّفات، وتفتح منفذًا
# صحيًّا لو PORT مضبوط (بعض المنصّات تُسقط الخدمة بدونه).
CMD ["python", "-u", "-m", "bot.main"]
