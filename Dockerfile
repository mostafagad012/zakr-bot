# بوت «ذاكر» — صورة Docker (تُستخدم على Railway أو أي VPS)
FROM python:3.13-slim

# لا حزم نظام لقراءة الصور: أُزيل مسار tesseract/poppler بالكامل.
# القراءة تتم عبر وكيل الرؤية، وتحويل PDF→صورة داخل بايثون بـPyMuPDF.
# فائدة جانبية: الصورة أخفّ بلا apt نهائيًا وأسرع في البناء.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY . .

ENV PYTHONUNBUFFERED=1
# فحص صحة حقيقي: العملية قد تكون "حيّة" وهي لا تسحب التحديثات (شبكة مقطوعة
# أو توكن مرفوض). نتحقق أن ملف العملية يشير لعملية غير زومبي وأن البوت
# ما زال يستجيب — و`restart: unless-stopped` يتكفّل بالإعادة.
# فحص صحة حقيقي: البوت يكتب data/.heartbeat كل دقيقة (heartbeat_loop).
# لو توقّفت النبضة ٥ دقائق فمعناه أن حلقة الاستقبال عالقة — والخدمة تُعاد.
# فحص صحة حقيقي: البوت يكتب `data/.heartbeat` كل دقيقة (heartbeat_loop)
# **بعد** نجاح الإقلاع. «العملية حيّة» ليست دليلًا: قد تكون عالقة في شبكة
# مقطوعة أو توكن مرفوض. لو تجمّدت النبضة ٥ دقائق تُعاد الخدمة.
# نشترط أيضًا وجود قاعدة البيانات، فإقلاع فاشل لا يترك نبضة قديمة حيّة.
HEALTHCHECK --interval=2m --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import os,sys,time; \
h='data/.heartbeat'; d='data/library_bot.db'; \
sys.exit(0 if (os.path.exists(h) and time.time()-os.path.getmtime(h) < 300 \
               and os.path.exists(d)) else 1)"

# مجلدات يكتب فيها البوت (قاعدة SQLite الافتراضية + نسخ احتياطية).
# على Railway نظام الملفات مؤقت: استخدم DATABASE_URL خارجيًا (Supabase)
# أو ركّب Volume على /app/data — انظر docs/OPERATIONS.md § Railway.
RUN mkdir -p data backups

# نقطة الدخول الموحّدة: تتحقّق من الحاجيات، تنظّف المخلّفات، وتفتح منفذًا
# صحيًّا لو PORT مضبوط (بعض المنصّات تُسقط الخدمة بدونه).
CMD ["python", "main.py"]
