# 🚀 طريقة تشغيل بوت «ذاكر» — بالبسيط

> اقرأ من فوق لتحت. مش محتاج تعرف برمجة. الوقت: **١٠ دقايق**.

---

## ٠) قبل ما تبدأ: ٣ حاجات بس

| # | هات إيه | من فين | شكله إيه |
|---|---|---|---|
| ١ | **توكن البوت** | [@BotFather](https://t.me/BotFather) ← `/newbot` ← اختار اسم ويوزرنيم | `7123456789:AAH...` |
| ٢ | **رقمك في تيليجرام** | ابعت أي رسالة لـ[@userinfobot](https://t.me/userinfobot) | `123456789` |
| ٣ | **قناة فاضية للتخزين** | اعمل قناة/جروب **خاص** جديد، وضيف البوت فيها **أدمن** بصلاحية «نشر» و«حذف» | `-1001234567890` |

> 💡 **ليه قناة تانية؟** البوت مش بيخزّن الملفات على السيرفر — بيبعتها لقناتك
> الخاصة دي ويحفظ «مكانها» بس. فالمساحة من تيليجرام، ومحدش يشوفها غيرك.

---

## ١) ⚡ الطريقة الأسرع: أمر واحد

```bash
unzip zakr-bot.zip -d zakr-bot
cd zakr-bot
python setup.py
```

`setup.py` و`bash setup.sh` مساران مستقلان: كل واحد يجهّز الاعتماديات،
وينشئ `.env` عند الحاجة، ويسألك تفاعليًا عن القيم الإلزامية الناقصة، ثم
يشغّل البوت. أدخل توكن تيليجرام بنفسك؛ لن يُطلب منك إرساله في المحادثة.

على Wispbyte استخدم `python main.py` كأمر بدء. إذا كانت Console تمرر الإدخال
للعملية، سيطلب البرنامج القيم الناقصة مباشرة؛ وإلا أدخلها في Variables. ويمكن
تفعيل `ZAKR_INTERACTIVE_SETUP=1` إذا كانت Console تدعم الإدخال رغم أن stdin
ليس TTY.

| الأمر | يعمل إيه |
|---|---|
| `python setup.py` | إعداد مستقل ببايثون + طلب القيم الناقصة في الطرفية + تشغيل البوت |
| `bash setup.sh` | إعداد مستقل بـBash + طلب القيم الناقصة في الطرفية + تشغيل البوت |
| `python main.py` | نقطة إقلاع مباشرة؛ تفحص القيم وتطلب الناقص من Console التفاعلية |
| `bash setup.sh --no-start` | تثبيت وتجهيز فقط بلا تشغيل |
| `bash setup.sh --check` | فحص البيئة بلا تثبيت |
| `bash setup.sh --test` | تثبيت + مجموعة الاختبارات الحالية (١٢ اختبار دخان) |
| `bash setup.sh --docker` | بناء وتشغيل عبر دوكر |
| `bash setup.sh --systemd` | خدمة تبقى شغّالة ٢٤ ساعة |
| `bash setup.sh --ocr-only` | حزم قراءة الملفات المصوّرة فقط |

**بلا أسئلة (للسيرفرات):**

```bash
ZAKR_BOT_TOKEN=... ZAKR_OWNER_ID=... ZAKR_STORAGE_CHANNEL=... bash setup.sh --start
```

> 🛡️ `.env` الموجود لا يُستبدل؛ تُحدّث القيم الناقصة فقط عند إدخالها.

## ١-ب) أو الخطوات اليدوية (لو مش عايز السكربت)

```bash
unzip zakr-bot.zip -d zakr-bot
cd zakr-bot

python3 -m venv .venv
source .venv/bin/activate        # على ويندوز:  .venv\Scripts\activate
pip install -r requirements.txt
```

> 🔧 محتاج **Python 3.11 أو أحدث** (المشروع مُختبر على 3.13).
> تحقّق: `python3 --version`

---

## ٢) قارئ الملفات المصوّرة (سطر واحد — مش إلزامي)

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-ara poppler-utils fonts-hosny-amiri
```

ده اللي بيخلي البوت يقرأ الـ PDF المصوّر (سكانر/فوتوغرافيا) بالعربي.

- 🍎 **ماك**: `brew install tesseract tesseract-lang poppler`
- 🪟 **ويندوز**: [مثبّت tesseract](https://github.com/UB-Mannheim/tesseract/wiki) واختار **Arabic** أثناء التثبيت + [poppler](https://github.com/oschwartz10612/poppler-windows/releases)، وضيف مسارهم لـ PATH.
- ⏭️ **لو كل ملفاتك PDF نصّي**: تقدر تتخطاها دلوقتي وتضيفها بعدين.

---

## ٣) الإعدادات: ٣ أسطر بس

```bash
cp .env.example .env
```

افتح `.env` بأي محرر نصوص وغيّر **٣ أسطر** — الباقي سيبه زي ما هو:

```env
BOT_TOKEN=التوكن اللي BotFather بعتهولك
OWNER_ID=رقمك من @userinfobot
STORAGE_CHANNEL=-100xxxxxxxxxx
```

### إزاي تجيب رقم القناة؟

1. ضيف البوت قناتك **كأدمن** أولًا.
2. بعدين:
   - قناة **عامة** (ليها يوزرنيم): `STORAGE_CHANNEL=@my_channel`
   - قناة **خاصة**: ضيف [@RawDataBot](https://t.me/RawDataBot) للقناة، هيبعتلك رقمًا شبه `-1001234567890` — ده اللي تحطه.

> 🟢 **بس كده.** البوت بيشتغل كامل **بلا أي مفتاح ذكاء اصطناعي** — المكتبة
> والبحث والرفع والكويزات كلها شغالة. المفاتيح اختيارية وبعدين.

---

## ٤) شغّله — اختار طريقة واحدة

### 🅰️ على جهازك (الأسهل للتجربة)

```bash
python main.py
```

لازم تشوف:

```text
البوت @your_bot يعمل (المالك: 123456789)
```

**سيب الشاشة مفتوحة** — البوت شغال طول ما الأمر ده شغال.
⏹️ **لإيقافه**: `Ctrl + C`.

---

### 🅱️ بدوكر (يشتغل في أي مكان بنفس البيئة)

```bash
docker build -t zakr-bot .
docker run -d --name zakr-bot --restart unless-stopped --env-file .env \
  -v "$(pwd)/data:/app/data" -v "$(pwd)/backups:/app/backups" zakr-bot
```

| الأمر | معناه |
|---|---|
| `--restart unless-stopped` | يرجع يشتغل لوحده بعد ريستارت السيرفر |
| `-v .../data` و`-v .../backups` | البيانات خارج الحاوية — متضيعش مع التحديث |

```bash
docker logs -f zakr-bot     # تتابع
docker stop zakr-bot        # توقّف
docker start zakr-bot       # تشغيل
```

---

### 🅲 على سيرفر دائم (systemd) — شغّال ٢٤ ساعة

```bash
sudo nano /etc/systemd/system/zakr-bot.service
```

```ini
[Unit]
Description=Zakr Study Bot
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/zakr-bot
ExecStart=/home/ubuntu/zakr-bot/.venv/bin/python /home/ubuntu/zakr-bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zakr-bot
journalctl -u zakr-bot -f        # تتابع
```

> ⚠️ غيّر `User` و`WorkingDirectory` لمسارك. وتأكد إن `.env` في نفس مجلد
> `WorkingDirectory` — البوت يقرأه من مجلد التشغيل.

---

### 🅳 على Railway (سحابي من المتصفح)

المشروع فيه `Dockerfile` و`railway.toml` جاهزين:

1. ارفع المشروع على **GitHub** (الخاص مقبول).
2. [railway.app](https://railway.app) ← **New Project** ← **Deploy from GitHub repo** ← اختار الريبو.
3. تبويب **Variables** ← ضيف: `BOT_TOKEN` · `OWNER_ID` · `STORAGE_CHANNEL` · `DATABASE_URL`.
4. **Deploy** وراقب **Deploy Logs** لحد `البوت @... يعمل`.

> ⚠️ حاجتان على السحابة: (١) ملفات Railway مؤقتة ⇒ لازم `DATABASE_URL` من
> [Supabase](https://supabase.com) بمنفذ **5432** (Session pooler)، وإلا
> البيانات تضيع مع كل نشر. (٢) **نسخة واحدة فقط** (`Replicas = 1`).

---

## ٥) تأكد إنه شغال (٣ اختبارات)

| # | اعمل إيه | لازم يحصل إيه | لو فشل |
|---|---|---|---|
| ١ | ابعت `/start` للبوت | يرد بقائمة أزرار | البوت مش شغّال أو التوكن غلط |
| ٢ | ابعت `/admin` | تظهر لوحة الإدارة | `OWNER_ID` غلط |
| ٣ | ارفع ملف صغير | يوصل طلب موافقة ويظهر بعدها | البوت مش أدمن في قناة التخزين |

**وبعدين جهّز محتوى:** `/admin` ← **📚 إدارة المواد** ← ➕ قسم جديد (مثلًا
«العلوم») ← جوه القسم ➕ مادة (مثلًا «فيزياء»).

---

## ٦) اختياري: شغّل الذكاء الاصطناعي (دقيقة)

مش محتاج تفتح `.env` ولا تعدّل كود:

1. `🛠️ لوحة الإدارة` ← `🤖 الذكاء الاصطناعي`
2. `🔑 إدارة المفاتيح` ← `➕ مفتاح groq` (أو أي مزوّد)
3. ابعت المفتاح — **رسالتك تتمسح فورًا**، والبوت يكتبه في `.env` باسم
   **`groq_api_key`** ويقولك **«✅ المفتاح يعمل»**.

المفتاح خلص رصيده أو عايز تدوّره؟ نفس الشاشة فيها **✏️ تحديث** ·
**🏷️ تغيير الاسم** · **🗑️ حذف (بتأكيد)** · **➕ إضافة**.

---

## ٧) أعطال شائعة (أول ١٠ دقايق)

| اللي شايفه | السبب | الحل |
|---|---|---|
| `BOT_TOKEN غير مُعدّ` | `.env` مش موجود أو فيه القيمة التجريبية | `cp .env.example .env` وعدّل السطر |
| `تيليجرام رفض BOT_TOKEN (Unauthorized)` | التوكن منسوخ ناقص أو فيه مسافة | انسخه من جديد من @BotFather |
| `ModuleNotFoundError` | الحاجيات مش مثبّتة أو البيئة مش مفعّلة | `source .venv/bin/activate` ثم `pip install -r requirements.txt` |
| `/admin` بيقول «للمشرفين فقط» | `OWNER_ID` غلط | راجع رقمك من @userinfobot |
| البوت مش بيرد | مش شغّال أو قفلت الشاشة | شغّله بـ systemd أو دوكر |
| `Conflict: terminated by other getUpdates` | نسختان شغّالتان بنفس التوكن | اقفل واحدة — نسخة واحدة فقط |
| البيانات اختفت على Railway | نظام ملفات مؤقت | `DATABASE_URL` من Supabase |
| OCR مش بيقرأ العربي | الخط العربي ناقص | `sudo apt-get install fonts-hosny-amiri` |
| الرفع بيرفض الملف | أكبر من ٢٠ ميجا | طبيعي — راجع قسم «ملفات أكبر من 20 ميجابايت» في README |

---

## ٨) أوامر تحتاجها

| الأمر | يعمل إيه |
|---|---|
| `python main.py` | يشغّل البوت على أي منصّة (الأمر الوحيد المطلوب) |
| `Ctrl + C` | يوقفه |
| `systemctl status zakr-bot` | حالته على السيرفر |
| `journalctl -u zakr-bot -f` | سجلّه الحيّ |
| `docker logs -f zakr-bot` | سجلّه على دوكر |
| `python -m pytest -q` | يجرّب المشروع كله (٥٨٣ اختبارًا) |

> 📌 **الأمر الوحيد جوه تيليجرام** اللي محتاج تحفظه هو `/start`. الباقي كله
> أزرار — بما فيه `/admin` اللي بيقابله زر «🛠️ لوحة الإدارة».

---

## مرجع سريع للصلاحيات

| الدور | يعمل إيه |
|---|---|
| **طالب** | تصفّح · رفع · بحث · شرح · امتحانات |
| **مشرف** | + الموافقة على الملفات · إدارة المحتوى |
| **أدمن** | + إعدادات الذكاء الاصطناعي وإدارة المفاتيح |
| **مالك** (`OWNER_ID`) | كل شيء + ترقية الناس |

الترقية: `/admin` ← **👥 المستخدمون** ← اختار الشخص ← **تغيير الدور**.

---

**التفاصيل التقنية الكاملة:** [`docs/OPERATIONS.md`](docs/OPERATIONS.md) ·
**المرجع الكامل:** [`README.md`](README.md)
