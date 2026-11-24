# تشغيل ذاكر على Wispbyte (وضع إلزامي كامل)

## إعداد Startup

- **Python:** 3.11 أو 3.12 (Linux Debian/Ubuntu، x86_64 موصى به).
- **Startup Command:**

```bash
python -u main.py
```

لا تستخدم `bot/main.py` مباشرة. `main.py` هو المشغّل الوحيد ويجري التثبيت والفحص قبل تشغيل البوت.

## المتغيرات المطلوبة

| المتغير | القيمة |
|---|---|
| `BOT_TOKEN` | توكن البوت من @BotFather |
| `OWNER_ID` | رقم حساب المالك |
| `STORAGE_CHANNEL` | آيدي قناة/جروب التخزين |

## ما يتم تثبيته إجباريًا

- aiogram وSQLAlchemy وaiosqlite وAlembic وpypdf وhttpx.
- asyncpg وPillow وNumPy وpytesseract وpdf2image وopenpyxl.
- ONNX Runtime وFastEmbed.
- Tesseract مع اللغات العربية والإنجليزية وبيانات OSD، وPoppler لتحويل PDF إلى صور.

إذا فشل تثبيت أي اعتماد Python أو أداة OCR أو لم يجتز التحقق الفعلي، **لن يبدأ البوت**. لن يتجاهل الفشل ولن يشغّل نسخة ناقصة.

## متطلبات صورة الحاوية وحدود Wispbyte

- يجب أن تكون صورة التشغيل Debian/Ubuntu متوافقة مع glibc، وبها `apt-get` و`dpkg-deb` أو أن تكون أدوات Tesseract/Poppler مثبتة مسبقًا.
- يجب أن تسمح الخطة بالاتصال الصادر إلى PyPI ومستودعات Debian وGitHub، وبمساحة كافية للتنزيل والتثبيت.
- OCR يضيف حجمًا ملحوظًا (Tesseract وPoppler وبيانات اللغة)، وFastEmbed/ONNX يزيدان حجم Python. هذا مقصود في وضع التثبيت الكامل.
- إذا كانت بيئة Wispbyte لا تسمح بتنزيل/تشغيل الملفات التنفيذية أو تمنع pip، فلا يوجد سكربت يمكنه تجاوز قيود الحاوية؛ سيظهر سبب الفشل في Console ويتوقف بدل ادعاء نجاح زائف.

## التخزين

- الملفات المرفوعة تحفظ في قناة/جروب Telegram.
- اربط Volume دائمًا على `/home/container/data` إن كانت الخطة تدعم ذلك، أو استخدم PostgreSQL عبر `DATABASE_URL`.
- شغّل نسخة واحدة فقط من البوت لكل توكن (Long Polling).

## النماذج الخارجية

تُثبّت المكتبات اللازمة للبحث الدلالي وفك التواء الصور. ملفات النماذج الكبيرة لا تُنزّل أثناء كل إعادة تشغيل؛ تُنزّل عند أول استخدام للميزة وتُحفظ في مساحة التخزين المتاحة. مفاتيح خدمات الذكاء الاصطناعي الخارجية تظل مطلوبة لتلك الخدمات ولا يمكن توليدها تلقائيًا.


## Startup commands (both supported)

Use either `python setup.py` or `bash setup.sh`. Each command independently installs and verifies the required Python packages and OCR system tools, validates the panel variables, and starts the bot. `python main.py` is also supported as the direct bootstrap entry point. On Wispbyte, `bash setup.sh` automatically selects strict panel mode.
