#!/usr/bin/env bash
# =============================================================================
#  بوت «ذاكر» — سكربت التشغيل الكامل (ينفّذ كل الخطوات مرة واحدة)
#
#  الاستخدام:
#      bash setup.sh              # يثبّت ويجهّز (ويعرض الخطوات)
#      bash setup.sh --no-start   # نفس الشيء صراحةً بلا تشغيل (نظير setup.py)
#      bash setup.sh --start       # يثبّت ثم يشغّل البوت فورًا
#      bash setup.sh --docker      # يبني ويشغّل عبر دوكر
#      bash setup.sh --systemd     # يثبّت خدمة systemd (٢٤ ساعة)
#      bash setup.sh --ocr-only    # يثبّت حزم OCR فقط
#      bash setup.sh --check       # يفحص البيئة بلا تثبيت
#      bash setup.sh --test        # يثبّت ثم يشغّل الاختبارات
#      bash setup.sh --wispbyte    # وضع لوحة استضافة (Wispbyte/Pterodactyl): كل شيء
#                                  #   آليًا ثم يشغّل البوت. استخدمه كـStartup Command.
#
#  على Wispbyte / Pterodactyl / أي لوحة:
#      Startup Command =  bash setup.sh --wispbyte  ← المسار الكامل
#      وأضف في تبويب Startup ← Variables:
#          BOT_TOKEN  ·  OWNER_ID  ·  STORAGE_CHANNEL
#      (لا حاجة لتعديل .env — متغيّرات اللوحة تفوز عليه)
#      بديل بـbash:      bash setup.sh --wispbyte
#      بديل مفعّل:        python setup.py --wispbyte
#
#  تشغيل آلي بلا أسئلة (مفيد للسيرفرات):
#      ZAKR_BOT_TOKEN=... ZAKR_OWNER_ID=... ZAKR_STORAGE_CHANNEL=... bash setup.sh --start
#      printf "TOKEN\nOWNER_ID\nCHANNEL\n" | bash setup.sh
#      bash setup.sh --token=... --owner=... --channel=...   (الأقل أمانًا: تظهر في ps)
#
#  ما يفعله بالترتيب:
#    1) يتحقق من بايثون (3.11+) ومن أدوات النظام
#    2) يثبّت حزم OCR (tesseract + العربية + poppler + خط عربي) إن أمكن
#    3) ينشئ بيئة افتراضية .venv ويثبّت requirements
#    4) ينشئ .env من .env.example ويسألك عن الأسطر الثلاثة الإلزامية
#    5) يبني قاعدة البيانات ويشغّل فحصًا ذاتيًا (بلا شبكة)
#    6) يشغّل البوت أو الاختبارات أو خدمة systemd حسب الطلب
#
#  آمن للتشغيل أكثر من مرة: لا يستبدل .env الموجود، ولا يكرّر التثبيت.
# =============================================================================
set -Eeuo pipefail

# بعض أدوات فكّ الضغط (أو نسخ الملفات عبر ويندوز/الشبكة) تُفقد صلاحية
# التنفيذ. نشغّل الأمر الأساسي هنا بدل أن نطالب المستخدم بـ chmod:
#   bash setup.sh --start   يعمل حتى لو الملف بلا صلاحية تنفيذ.
if [[ ! -x "${BASH_SOURCE[0]}" ]]; then
  chmod +x "${BASH_SOURCE[0]}" 2>/dev/null || true
fi

# ------------------------------------------------ ثوابت ومخرجات ملوّنة
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
VENV_DIR_REL=".venv"
VENV_REAL=""            # المسار الفعلي للبيئة (يُحدَّد في setup_venv_location)
VENV_REAL_PY=""
ENV_FILE=".env"
ENV_EXAMPLE=".env.example"
PY_MIN_MINOR=11            # 3.11+
SERVICE_NAME="zakr-bot"
DOCKER_IMAGE="zakr-bot"
OCR_PACKAGES=(tesseract-ocr tesseract-ocr-ara poppler-utils fonts-hosny-amiri)

# --------------------------------------------------------------- البيئة
# Termux (أندرويد) ليس دبيانًا ولا ماك: مدير الحزم `pkg`، وأسماء الحزم
# مختلفة (لا وجود لـ tesseract-ocr-ara)، و`onnxruntime` بلا حزمة أندرويد
# أصلًا، وبايثون داخل venv لا يرى حزم `pkg` إلا بـ --system-site-packages.
# لذلك نفصل المسار بدلًا من محاولة «إنقاذ» أوامر Debian على أندرويد —
# تفشل بصمت وتُربك المستخدم.
# Wispbyte / Pterodactyl: ملفات المشروع في /home/container، ولا صلاحية
# root ولا apt، والإعدادات تأتي كمتغيّرات بيئة من اللوحة لا من .env.
IS_PANEL=0
if [[ -n "${WISPBYTE:-}" ]] || [[ -n "${PTERODACTYL:-}" ]] || [[ -d /home/container && ! -L /home/container ]]; then
  IS_PANEL=1
fi

IS_TERMUX=0
if [[ -n "${TERMUX_VERSION:-}" ]] || [[ "${PREFIX:-}" == *com.termux* ]]; then
  IS_TERMUX=1
fi

OCR_PACKAGES_TERMUX=(tesseract poppler python-pillow python-numpy)
# حاجيات لا تُبنى على أندرويد (لا عجلات aarch64 على PyPI): تُؤخذ من `pkg`.
# (ملاحظة: القائمة الفعلية للتخطّي تُبنى داخل termux_requirements — لا تكرار)

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'
  C_ERR=$'\033[1;31m'; C_HEAD=$'\033[1;36m'; C_DIM=$'\033[2m'
else
  C_RESET=""; C_OK=""; C_WARN=""; C_ERR=""; C_HEAD=""; C_DIM=""
fi

ok()   { printf '%s✔%s %s\n' "$C_OK" "$C_RESET" "$*"; }
warn() { printf '%s⚠%s %s\n' "$C_WARN" "$C_RESET" "$*"; }
info() { printf '%s•%s %s\n' "$C_DIM" "$C_RESET" "$*"; }
head_() { printf '\n%s== %s ==%s\n' "$C_HEAD" "$*" "$C_RESET"; }

# كل خطأ يطبع سطرًا مفهومًا بدل تتبّع غامض
trap 'printf "\n%s✖ فشل السكربت عند السطر %s%s\n" "$C_ERR" "$LINENO" "$C_RESET" >&2' ERR

die() { printf '\n%s✖ %s%s\n' "$C_ERR" "$*" "$C_RESET" >&2; exit 1; }

ask() {  # ask "السؤال" "القيمة الافتراضية" → يطبع الإجابة
  # NO_INPUT=1 (من --no-input) ⇒ لا نسأل شيئًا ونتخطّى
  if [[ "${NO_INPUT:-0}" == "1" ]]; then printf '%s' "${2:-}"; return; fi
  # ثلاث حالات: (أ) طرفية تفاعلية ⇒ نسأل، (ب) أنبوب/ملف ⇒ نقرأ سطرًا،
  # (ج) لا مدخل ⇒ نتخطّى. الحالة (ب) ضرورية للتشغيل الآلي بلا تفاعل:
  #     printf "TOKEN\nOWNER\nCHANNEL\n" | ./setup.sh
  local prompt="$1" default="${2:-}" answer=""
  if [[ -t 0 ]]; then
    if [[ -n "$default" ]]; then
      read -r -p "$(printf '%s%s%s [%s]: ' "$C_HEAD" "$prompt" "$C_RESET" "$default")" answer || true
    else
      read -r -p "$(printf '%s%s%s: ' "$C_HEAD" "$prompt" "$C_RESET")" answer || true
    fi
  else
    IFS= read -r answer || true     # EOF ⇒ تخطّي بهدوء
  fi
  printf '%s' "${answer:-$default}"
}

have() { command -v "$1" >/dev/null 2>&1; }

# تنزيل ملف: curl ثم wget (كلاهما قد يغيب على أنظمة مصغّرة)
download_file() {  # download_file URL DEST
  local url="$1" dest="$2"
  if have curl; then
    curl -fsSL --retry 3 --max-time 120 -o "$dest" "$url" 2>/dev/null && return 0
  fi
  if have wget; then
    wget -q -T 120 -O "$dest" "$url" 2>/dev/null && return 0
  fi
  return 1
}



# ------------------------------------------------ قفل ضد التشغيل المتزامن
# عِلّة حقيقية: لو ضغطت الأمر مرتين (أو نسيت نافذة مفتوحة)، النسختان
# تعملان على نفس .venv فيتعارض pip (قفل/ملفات نصف مكتوبة) وتفشل واحدة
# برسالة «فشل تثبيت الحاجيات» بلا سبب واضح للمستخدم.
LOCK_DIR=".setup.lock"

acquire_lock() {
  local tries=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    # قفل قديم من تشغيل انتهى فجأة (Ctrl+C / انقطاع): نفحص PID
    if [[ -f "$LOCK_DIR/pid" ]]; then
      local old_pid; old_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")"
      if [[ -n "$old_pid" ]] && ! kill -0 "$old_pid" 2>/dev/null; then
        warn "قفل قديم من تشغيل سابق (انتهى بلا إغلاق) — سأزيله"
        rm -rf -- "$LOCK_DIR"
        continue
      fi
      if [[ -z "$old_pid" ]]; then
        # قفل بلا PID (لحظة إنشاء) — ننتظر قليلًا
        sleep 1
      fi
    fi
    tries=$((tries + 1))
    if (( tries == 1 )); then
      info "السكربت يعمل بالفعل في نافذة أخرى — أنتظر انتهاءه (حتى ٩٠ ثانية)…"
    fi
    if (( tries > 90 )); then
      die "ما زال سكربت آخر يعمل. لو تأكدت أنه غير شغّال:
   rm -rf $LOCK_DIR && bash setup.sh"
    fi
    sleep 1
  done
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  # نضمن إزالة القفل عند أي خروج (نجاح/خطأ/Ctrl+C)
  trap 'rm -rf -- "$LOCK_DIR" 2>/dev/null' EXIT INT TERM
}

# ------------------------------------------------ 1) فحص بايثون
python_ok() {  # يطبع مسار بايثون الصالح (3.11+) أو يفشل
  # على Termux لا يوجد python3.13/python3.12 بأسماء منفصلة — بايثون واحد
  # يُثبَّت بـ`pkg install python`. لذلك نجرّب الأسماء العامة أولًا هناك.
  local candidates
  if (( IS_TERMUX )); then
    candidates=(python python3 python3.13 python3.12 python3.11)
  else
    candidates=(python3.13 python3.12 python3.11 python3 python)
  fi
  local candidate
  for candidate in "${candidates[@]}"; do
    if have "$candidate"; then
      if "$candidate" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $PY_MIN_MINOR) else 1)" 2>/dev/null; then
        printf '%s' "$candidate"; return 0
      fi
    fi
  done
  return 1
}

step_python() {
  head_ "1/6 فحص بايثون"
  local py
  if ! py="$(python_ok)"; then
    die "محتاج Python 3.$PY_MIN_MINOR أو أحدث.
   أوبنتو/دبيان:  sudo apt-get install -y python3 python3-venv python3-pip
   ماك:          brew install python@3.12
   ويندوز:       https://python.org/downloads (علّم «Add python.exe to PATH»)
   أندرويد:      pkg install python   (ثم: bash setup.sh --start)"
  fi
  PYTHON="$py"
  ok "$("$PYTHON" --version 2>&1) → $(command -v "$PYTHON")"

  # venv و pip ضروريان على دبيان/أوبنتو (حزمة منفصلة)
  if ! "$PYTHON" -c "import venv, ensurepip" >/dev/null 2>&1; then
    if (( IS_TERMUX )); then
      warn "وحدة venv ناقصة — على أندرويد: pkg install python"
    else
      warn "وحدة venv ناقصة — على أوبنتو: sudo apt-get install -y python3-venv"
    fi
  fi
}

# ------------------------------------------------ 2) حزم النظام (OCR)
# =============================================================================
#  التحقق **الفعلي** من OCR — لا يكفي أن الأمر موجود، لازم يشتغل فعلًا
# =============================================================================
#  عِلّة حقيقية وقعت: المستخدم ثبّت tesseract و poppler، والسكربت يقول
#  «غير مثبّتين» أو البوت يقول «OCR غير متاح». و`shutil.which`/`have` لا
#  تكشف الأسباب الحقيقية:
#    • tesseract موجود لكن مكتبة مشتركة ناقصة/معطوبة → يظهر في PATH ويفشل
#      عند التشغيل (رسالة ld.so) — inutile تمامًا لو اكتفينا بـwhich.
#    • pdftoppm موجود لكنه لا يقدر يقرأ PDF (صلاحيات/مكتبة) → نفس المشكلة.
#    • بيانات اللغة العربية (`ara.traineddata`) غير موجودة → OCR «يعمل»
#      لكنه يفشل على كل صفحة عربية ويرجع نصًا فارغًا بهدوء.
#  لذلك: نُشغّل الأدوات فعليًا ونقرأ مخرجاتها.
OCR_PROBE_PDF=""

ocr_probe_prepare() {   # ينشئ PDF صفحة واحدة لاختبار حقيقي
  [[ -n "$OCR_PROBE_PDF" && -f "$OCR_PROBE_PDF" ]] && return 0
  local d tmp
  d="$(mktemp -d 2>/dev/null)" || return 1
  # PDF يدوي صغير بصفحة واحدة (بلا مكتبات) — كافٍ لاختبار pdftoppm/tesseract
  python3 - "$d/probe.pdf" <<'PYEOF' 2>/dev/null || return 1
import sys, zlib
objs = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
]
stream = b"BT /F1 24 Tf 20 40 Td (OCR PROBE 12345) Tj ET"
objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
out = bytearray(b"%PDF-1.4\n")
offs = []
for i, o in enumerate(objs, 1):
    offs.append(len(out))
    out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
xref = len(out)
out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
for off in offs:
    out += b"%010d 00000 n \n" % off
out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
open(sys.argv[1], "wb").write(out)
PYEOF
  [[ -f "$d/probe.pdf" ]] || return 1
  OCR_PROBE_PDF="$d/probe.pdf"
  export OCR_PROBE_DIR="$d"
}

# يرجع: 0 = يعمل فعلًا، 1 = غير مثبّت، 2 = مثبّت لكن معطوب، 3 = بلا عربية
ocr_verify() {
  # ١) tesseract: موجود في PATH؟
  if ! have tesseract; then
    info "tesseract غير مثبّت"
    return 1
  fi
  # ٢) tesseract: يعمل فعلًا؟ (mكتبة ناقصة/ld.so → يفشل هنا)
  local tv
  # ⚠️ لا تستخدم `if ! cmd="$(… | head)"` — `head` يُنهي الأنبوب بنجاح فيُخفي
  #    فشل tesseract. نلتقط كود الخروج من tesseract نفسه.
  #    (PIPESTATUS لا يعبر حدود $( ) — لذلك لا نستخدم أنبوبًا هنا أبدًا)
  local trc
  tv="$(tesseract --version 2>&1)"; trc=$?
  tv="$(printf '%s\n' "$tv" | head -2)"
  if (( trc != 0 )) || [[ -z "$tv" ]]; then
    warn "tesseract موجود في PATH لكنه لا يعمل"
    if [[ -n "$tv" ]]; then
      info "السبب:"; printf '%s\n' "$tv" | while IFS= read -r l; do [[ -n "$l" ]] && info "  $l"; done
    else
      info "السبب: الملف موجود لكنه لا ينفّذ (تلف/صلاحية/مكتبة ناقصة)"
    fi
    return 2
  fi
  tv="${tv%%$'\n'*}"
  info "tesseract يعمل: $tv"

  # ٣) pdftoppm: موجود ويعمل فعلًا؟ (نختبره على PDF حقيقي)
  if ! have pdftoppm; then
    info "pdftoppm غير مثبّت (تحتاجه الملفات المصوّرة PDF)"
    return 1
  fi
  if ocr_probe_prepare; then
    local out_img="$OCR_PROBE_DIR/probe"
    if pdftoppm -r 100 -png -f 1 -l 1 "$OCR_PROBE_PDF" "$out_img" >/dev/null 2>&1 \
       && ls "$out_img"*.png >/dev/null 2>&1; then
      ok "pdftoppm يعمل فعلًا (حوّل PDF إلى صورة)"
    else
      warn "pdftoppm موجود لكنه فشل في تحويل PDF فعلي"
      local perr; perr="$(pdftoppm -r 100 -png -f 1 -l 1 "$OCR_PROBE_PDF" "$out_img" 2>&1 | head -3)"
      printf '%s\n' "$perr" | while IFS= read -r l; do [[ -n "$l" ]] && info "  $l"; done
      return 2
    fi
  else
    info "تعذّر تجهيز PDF اختباري — أتخطّى الاختبار الفعلي لـpdftoppm"
  fi

  # ٤) اللغة العربية: موجودة فعلًا؟ ونقرأ بها فعلًا؟
  local langs; langs="$(tesseract --list-langs 2>/dev/null | tr '\n' ' ' || true)"
  if [[ "$langs" != *ara* ]]; then
    warn "بيانات اللغة العربية (ara.traineddata) غير موجودة"
    info "OCR الإنجليزي يعمل، لكن كل صفحة عربية سترجع نصًا فارغًا بهدوء."
    return 3
  fi
  ok "بيانات العربية موجودة (ara)"

  # ٥) أهم اختبار: هل tesseract يقرأ صورة فعلية فعلًا؟
  local img=""
  img="$(find "$OCR_PROBE_DIR" -maxdepth 1 -name 'probe*.png' -print -quit 2>/dev/null || true)"
  if [[ -n "$img" && -f "$img" ]]; then
      local read_out; read_out="$(tesseract "$img" stdout 2>/dev/null | tr -d '[:space:]' || true)"
      if [[ -n "$read_out" ]]; then
        ok "tesseract قرأ صورة فعلية بنجاح"
      else
        warn "tesseract لم يقرأ أي نص من صورة اختبارية واضحة"
        info "غالبًا بيانات اللغة (tessdata) ناقصة أو TESSDATA_PREFIX خاطئ."
        return 2
      fi
  fi
  return 0
}

ocr_probe_cleanup() {
  [[ -n "${OCR_PROBE_DIR:-}" && -d "${OCR_PROBE_DIR:-}" ]] && rm -rf "$OCR_PROBE_DIR"
  OCR_PROBE_PDF=""
}

# =============================================================================
#  2) تثبيت قارئ الملفات المصوّرة — بأسماء **كل منصّة** الصحيحة
# =============================================================================
step_ocr() {
  head_ "2/6 قارئ الملفات المصوّرة (اختياري)"

  local rc=0
  ocr_verify || rc=$?

  if (( rc == 0 )); then
    ok "tesseract + poppler + العربية: كلها تعمل فعلًا"
    ocr_probe_cleanup
    return 0
  fi

  # ── التثبيت: لكل منصّة أسماؤها الحقيقية (لا أوامر ديبيان على أندرويد)
  if (( IS_TERMUX )); then
    info "أندرويد/Termux — أسماء الحزم مختلفة عن ديبيان:"
    info "  المطلوب: ${OCR_PACKAGES_TERMUX[*]}  (ولا وجود لـtesseract-ocr-ara)"
    if pkg install -y "${OCR_PACKAGES_TERMUX[@]}" >/dev/null 2>&1; then
      ok "ثُبّتت tesseract و poppler من pkg"
    else
      warn "تعذّر التثبيت التلقائي — نفّذ يدويًا:"
      info "  pkg install ${OCR_PACKAGES_TERMUX[*]}"
    fi
  elif have apt-get; then
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
      info "تثبيت حزم OCR (كجذر)…"
      if apt-get update -qq \
        && apt-get install -y --no-install-recommends "${OCR_PACKAGES[@]}"; then
        ok "ثُبّتت حزم OCR"
      else
        warn "تعذّر التثبيت"
      fi
    elif have sudo && sudo -n true 2>/dev/null; then
      info "تثبيت حزم OCR (بـ sudo)…"
      if sudo apt-get update -qq \
        && sudo apt-get install -y --no-install-recommends "${OCR_PACKAGES[@]}"; then
        ok "ثُبّتت حزم OCR"
      else
        warn "تعذّر التثبيت"
      fi
    else
      info "تحتاج صلاحية التثبيت. نفّذ يدويًا:"
      info "  sudo apt-get install -y ${OCR_PACKAGES[*]}"
    fi
  elif have brew; then
    info "ماك: brew install tesseract tesseract-lang poppler"
    brew install tesseract tesseract-lang poppler >/dev/null 2>&1 || warn "تعذّر brew"
  else
    info "ويندوز/غير معروف: ثبّت tesseract (مع Arabic) + poppler يدويًا وأضفهم لـPATH"
  fi

  # ── بيانات العربية: ننزّلها إن كانت ناقصة (على كل المنصّات، لا أندرويد فقط)
  ocr_fetch_arabic_data || true

  # ── إعادة التحقق الفعلي بعد التثبيت
  ocr_probe_cleanup
  local rc2=0
  ocr_verify || rc2=$?
  ocr_probe_cleanup

  if (( rc2 == 0 )); then
    ok "قارئ الملفات المصوّرة جاهز ومُختبَر"
  else
    warn "قارئ الملفات المصوّرة غير جاهز — البوت يعمل، لكن السكانر لن يُقرأ"
    ocr_troubleshoot
  fi
  return 0
}

# ينزّل ara.traineddata في المكان الصحيح لكل منصّة (بلا إذن كتابة → يرشد)
ocr_fetch_arabic_data() {
  local tessdata
  if (( IS_TERMUX )); then
    tessdata="${PREFIX:-/data/data/com.termux/files/usr}/share/tessdata"
  else
    # نستنتج المكان من tesseract نفسه لا بالتخمين.
    # (tesseract 4/5 لا يملك --print-tessdata-dir — لا تعتمد عليه)
    tessdata=""
    local tsl; tsl="$(tesseract --list-langs 2>/dev/null | head -1 || true)"
    if [[ "$tsl" == *tessdata*/* || "$tsl" == *tessdata* ]]; then
      # مثل: «List of available languages in /usr/share/tesseract-ocr/5/tessdata/ (2):»
      tessdata="$(printf '%s' "$tsl" | sed -n 's|.*\(/.*tessdata\)/\?.*|\1|p')"
    fi
    if [[ -z "$tessdata" || ! -d "$tessdata" ]]; then
      tessdata=""
      for d in /usr/share/tesseract-ocr/5/tessdata /usr/share/tesseract-ocr/4.00/tessdata \
               /usr/share/tessdata /usr/local/share/tessdata \
               /opt/homebrew/share/tessdata "${HOME}/.local/share/tessdata"; do
        [[ -d "$d" ]] && { tessdata="$d"; break; }
      done
    fi
    # احتياط أخير: أي مكان فيه eng.traineddata
    if [[ -z "$tessdata" ]]; then
      local found
      found="$(find /usr/share /usr/local/share -name "eng.traineddata" 2>/dev/null | head -1 || true)"
      [[ -n "$found" ]] && tessdata="$(dirname "$found")"
    fi
  fi
  [[ -n "$tessdata" ]] || { warn "تعذّر تحديد مجلد tessdata"; return 1; }

  if [[ -d "$tessdata" && -f "$tessdata/ara.traineddata" ]]; then
    ok "بيانات العربية موجودة في $tessdata"
    return 0
  fi

  info "تنزيل بيانات اللغة العربية (ara) ≈ ١.٥ ميجا …"
  local url="https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/ara.traineddata"
  local tmpf; tmpf="$(mktemp 2>/dev/null || echo "./.ara.download")"
  if curl -fsSL --retry 3 -o "$tmpf" "$url" 2>/dev/null; then
    if cp "$tmpf" "$tessdata/ara.traineddata" 2>/dev/null; then
      ok "بيانات العربية جاهزة في $tessdata"
      rm -f "$tmpf"; return 0
    fi
    warn "نزّلت الملف لكن لا أملك صلاحية الكتابة في $tessdata"
    info "نفّذ: sudo cp $tmpf $tessdata/ara.traineddata"
    return 1
  fi
  warn "تعذّر تنزيل بيانات العربية (شبكة؟)"
  info "  يدويًا: curl -L -o \$TESSDATA/ara.traineddata $url"
  rm -f "$tmpf" 2>/dev/null
  return 1
}

# رسالة تشخيص دقيقة عند بقاء المشكلة — تقول **السبب** لا «فشل»
ocr_troubleshoot() {
  local t="", p=""
  have tesseract && t="موجود" || t="غير مثبّت"
  have pdftoppm && p="موجود" || p="غير مثبّت"
  info "التشخيص: tesseract=$t · pdftoppm=$p"
  if ! have tesseract && ! have pdftoppm; then
    if (( IS_TERMUX )); then
      info "  نفّذ: pkg install ${OCR_PACKAGES_TERMUX[*]}"
    else
      info "  نفّذ: sudo apt-get install -y ${OCR_PACKAGES[*]}"
    fi
  elif have tesseract; then
    info "  جرّب الأمرين لرؤية السبب بنفسك:"
    info "    tesseract --version"
    info "    tesseract --list-langs | grep ara"
  fi
  info "تأكد أن مجلد tessdata فيه ara.traineddata، أو عيّن TESSDATA_PREFIX."
}

# ------------------------------------------------ 3) البيئة الافتراضية والحاجيات
# =============================================================================
#  إعداد مسار البيئة الافتراضية (مسار آمن للتخزين المشترك على أندرويد)
# =============================================================================
#  عِلّة حقيقية يقع فيها كل من ينزّل المشروع على الهاتف:
#
#    تخزين أندرويد المشترك (~/storage/... = /sdcard = /storage/emulated/0)
#    **لا يدعم الروابط الرمزية ولا صلاحية التنفيذ**. و`python -m venv` يعتمد
#    على الاثنين: ينشئ bin/python كرابط رمزي لبايثون النظام، ويجعل الملفات
#    قابلة للتنفيذ. فيفشل الإنشاء دائمًا برسالة مثل:
#        Unable to symlink ... PermissionError: [Errno 13]
#
#  الحل الصحيح (وليس التخطّي): نضع البيئة **في مجلد Termux الخاص**
#  (~/.local/share/zakr-bot/venv) حيث POSIX كامل، ونترك في مجلد المشروع
#  ملفًا صغيرًا باسم .venv/bin/python يوجّه إلى بيئة المستخدم. فتبقى كل
#  أوامر التوثيق تعمل كما هي (source .venv/bin/activate).
#
#  وميزة إضافية مهمة: حذف مجلد المشروع أو استبداله لا يفقد البيئة.
setup_venv_location() {
  local want_local=0

  if (( IS_TERMUX )); then
    case "$SCRIPT_DIR" in
      "${PREFIX:-/data/data/com.termux/files/usr}"/*|\
      "$HOME"/.local/share/*)   want_local=0 ;;   # داخل مساحة Termux: عادي
      *)                        want_local=1 ;;   # storage/sdcard ⇒ بيئة خارجية
    esac
  fi

  if (( want_local )); then
    local base="${HOME:-$PREFIX}/.local/share/zakr-bot"
    VENV_REAL="$base/venv"
    VENV_REAL_PY="$VENV_REAL/bin/python"
    ok "بيئة افتراضية خارج التخزين المشترك"
    info "المسار: $VENV_REAL"
    info "(التخزين المشترك على أندرويد لا يدعم الروابط التي تحتاجها البيئة)"
  else
    VENV_REAL="$SCRIPT_DIR/$VENV_DIR_REL"
    VENV_REAL_PY="$VENV_REAL/bin/python"
  fi
}

# هل البيئة في المشروع أم في مجلد المستخدم؟
venv_is_external() { [[ "$VENV_REAL" != "$SCRIPT_DIR/$VENV_DIR_REL" ]]; }

# هل البيئة سليمة؟ (نفحص المسار الحقيقي — لا نثق بوجود المجلد)
venv_is_valid() {
  local py="$VENV_REAL_PY"
  [[ -x "$py" ]] || py="$VENV_REAL/Scripts/python.exe"
  [[ -x "$py" ]] || return 1
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    >/dev/null 2>&1 || return 1
  return 0
}

# حذف البيئة بأمان: المسار الحقيقي + واجهة .venv في المشروع (إن وُجدت)
remove_venv() {
  local real="${VENV_REAL:?}"
  case "$real" in
    "$SCRIPT_DIR/$VENV_DIR_REL"|"${HOME:-/nonexistent}"/.local/share/zakr-bot/venv) ;;
    *) die "مسار بيئة غير متوقّع ($real) — لن أحذف شيئًا." ;;
  esac
  [[ -e "$real" ]] && rm -rf -- "${real:?}"
  # واجهة .venv في المشروع: نحذفها فقط لو كانت روابط/سكربتات أنشأناها
  if [[ -n "$SCRIPT_DIR" && -d "$SCRIPT_DIR/$VENV_DIR_REL" ]]; then
    case "$SCRIPT_DIR" in
      /*) rm -rf -- "${SCRIPT_DIR:?}/${VENV_DIR_REL:?}" ;;
    esac
  fi
  ok "حُذفت البيئة القديمة"
}

# إنشاء .venv/bin/python و .venv/bin/activate كواجهة للبيئة الخارجية
link_venv_facade() {
  venv_is_external || return 0
  local facade="$SCRIPT_DIR/$VENV_DIR_REL"
  mkdir -p "$facade/bin" 2>/dev/null || {
    warn "تعذّر إنشاء واجهة .venv (سيُستخدم المسار الكامل)"
    return 0
  }

  # ١) روابط رمزية إن أمكن
  if ln -sfn "$VENV_REAL_PY" "$facade/bin/python" 2>/dev/null; then
    ln -sfn "$VENV_REAL_PY" "$facade/bin/python3" 2>/dev/null || true
    if [[ -f "$VENV_REAL/bin/activate" ]]; then
      ln -sfn "$VENV_REAL/bin/activate" "$facade/bin/activate" 2>/dev/null || true
    fi
    return 0
  fi

  # ٢) التخزين المشترك لا يقبل الروابط ⇒ سكربتات بسيطة (تنفيذية على نظام
  #    يدعم التنفيذ؛ وعلى أندرويد نستخدم مسار البيئة مباشرة في setup.sh)
  {
    printf '#!/bin/sh\n'
    printf 'exec "%s" "$@"\n' "$VENV_REAL_PY"
  } > "$facade/bin/python" 2>/dev/null || true
  chmod +x "$facade/bin/python" 2>/dev/null || true
  {  # shellcheck disable=SC2016  # التوسيع مقصود في وقت تشغيل الملف الناتج
    printf '# استخدم: source .venv/bin/activate\n'
    printf 'VENV="%s"\n' "$VENV_REAL"
    # shellcheck disable=SC2016  # التوسيع مطلوب في وقت تشغيل activate لا الآن
    printf 'if [ -f "$VENV/bin/activate" ]; then . "$VENV/bin/activate"; fi\n'
  } > "$facade/bin/activate" 2>/dev/null || true
}


step_venv() {
  head_ "3/6 البيئة الافتراضية والحاجيات"

  # علامة من تشغيل سابق: تعذّر إنشاء pip داخل بيئة، فقرّرنا العمل ببايثون
  # النظام. بدون هذه العلامة يعيد السكربت المحاولة كل مرة (وميض بلا فائدة
  # على أجهزة معطوبة البيئة) — فنحترم القرار السابق.
  if [[ -f ".no-venv" && "${FORCE_VENV:-0}" != "1" ]] && "$PYTHON" -m pip --version >/dev/null 2>&1; then
    ok "تخطّي البيئة الافتراضية (سبق تعذّرها — نعمل ببايثون النظام)"
    info "الحاجيات في مجلد المستخدم. لإعادة المحاولة: rm .no-venv && bash setup.sh"
    VENV_PY="$PYTHON"
    VENV_REAL=""
    setup_venv_location
    step_venv_install
    return 0
  fi

  # 🔴 عطل حقيقي ظهر على أندرويد: «تعذّر إنشاء البيئة الافتراضية».
  #   السبب ليس البوت: `python -m venv` يستدعي ensurepip لتثبيت pip داخل
  #   البيئة، و**ensurepip يفشل إذا كان pip موجودًا في حزم النظام مع
  #   --system-site-packages** (عِلّة CPython معروفة: bpo-24875). وعلى Termux
  #   pip موجود في حزم pkg بالضرورة. الحل الصحيح: **لا نعتمد على ensurepip
  #   أبدًا** — ننشئ البيئة بـ--without-pip ثم نجهّز pip بسُلّم من ٥ درجات.

  setup_venv_location

  if [[ -d "$VENV_REAL" || -d "$VENV_DIR_REL" ]]; then
    if venv_is_valid; then
      ok "البيئة موجودة وسليمة"
    else
      warn "مجلد $VENV_DIR موجود لكن بايثون داخله ناقص أو مكسور"
      info "ذلك يحدث لو أُنشئت البيئة قبل تثبيت بايثون، أو بعد تحديثه."
      info "سأحذف المجلد وأعيد بناءه من جديد (لا يمسّ هذا .env ولا بياناتك)."
      remove_venv
    fi
  fi

  if [[ ! -d "$VENV_DIR" ]]; then
    info "إنشاء $VENV_DIR …"
    local venv_args=()
    (( IS_TERMUX )) && venv_args=(--system-site-packages)

    mkdir -p "$(dirname "$VENV_REAL")" 2>/dev/null || true

    # ⚠️ لا نُخفي رسالة بايثون: خطأ الفشل هنا هو أهمّ معلومة للمستخدم
    #    (كان يُخفيها 2>/dev/null فيظهر «تعذّر» بلا سبب أبدًا).
    local venv_err=".venv-error.log"
    if "$PYTHON" -m venv --without-pip "${venv_args[@]}" "$VENV_REAL" 2>"$venv_err"; then
      :
    else
      rm -rf -- "$VENV_REAL"
      if ! "$PYTHON" -m venv "${venv_args[@]}" "$VENV_REAL" 2>"$venv_err"; then
        rm -rf -- "$VENV_REAL"
        local detail
        detail="$(tail -6 "$venv_err" 2>/dev/null | sed 's/^/     /')"
        rm -f "$venv_err"
        die "تعذّر إنشاء البيئة الافتراضية.

   سبب بايثون:
$detail

   أشهر سبب على أندرويد: المشروع في التخزين المشترك (~/storage/…)
   وهو لا يدعم الروابط الرمزية ولا صلاحية التنفيذ — والبيئة تحتاجهما.
   الحل: انقل المشروع لمجلد Termux الخاص:
       cp -r ~/storage/downloads/zakr-bot ~/zakr-bot && cd ~/zakr-bot
       bash setup.sh
   (السكربت يحاول وضع البيئة في ~/.local/share/zakr-bot/venv تلقائيًا،
    فإن فشل حتى ذلك فالسبب بيئي: pkg install python ثم أعد المحاولة)"
      fi
    fi
    rm -f "$venv_err"
    ok "أُنشئت البيئة"
  fi

  link_venv_facade
  VENV_PY="$VENV_REAL_PY"
  [[ -x "$VENV_PY" ]] || VENV_PY="$VENV_REAL/Scripts/python.exe"
  if [[ ! -x "$VENV_PY" ]]; then
    die "البيئة أُنشئت لكن بلا بايثون داخلها — خطأ غير متوقّع.
   احذفها وأعد: rm -rf .venv && bash setup.sh"
  fi

  if venv_has_pip; then
    ok "pip جاهز داخل البيئة"
  else
    info "pip غير موجود داخل البيئة (طبيعي: أنشأناها بلا pip) — سأثبّته …"
    if ensure_venv_pip; then
      ok "pip جاهز"
    else
      warn "تعذّر تجهيز pip داخل البيئة — سأكمل ببايثون النظام"
      info "الحاجيات ستُثبَّت في مجلد المستخدم (~/.local) بدل البيئة."
      info "البوت يعمل بهذه الطريقة تمامًا — النتيجة نفسها."
      VENV_PY="$PYTHON"
      # بيئة بلا pip لا فائدة منها، ووجودها يُربك التشغيل التالي ⇒ نحذفها
      remove_venv >/dev/null 2>&1 || true
      VENV_REAL=""
      printf '%s\n' "تعذّر تجهيز pip داخل .venv — نعمل ببايثون النظام." > .no-venv
      if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
        die "لا يوجد pip على النظام أصلًا.
   أندرويد:  pkg install python-pip
   أوبنتو:   sudo apt-get install -y python3-pip
   ثم أعد: bash setup.sh"
      fi
    fi
  fi

  step_venv_install
}


# تثبيت الحاجيات في البيئة الحالية (VENV_PY) — مشتركة بين المسارين
# =============================================================================
#  تثبيت الحاجيات — **إجباري وكامل**، عبر مُثبِّت واحد مشترك
# =============================================================================
#  سبب وجود setup_deps.py: كل من setup.sh (bash) وsetup.py (python) يجب أن
#  يثبّت **نفس الشيء بنفس الطريقة**. مصدر واحد للحقيقة يمنع أن ينجح أحدهما
#  ويفشل الآخر على نفس السيرفر.
#
#  المُثبِّت يثبّت **واحدة واحدة** لا `-r requirements.txt` دفعة واحدة: لو
#  فشلت حزمة، نعرف اسمها وسببها ونعيد المحاولة ببديل — بدل رسالة مبهمة عن
#  القائمة كلها (وهذا ما أسقط التثبيت سابقًا على الجوال).
step_venv_install() {
  info "تثبيت الحاجيات (إجباري — واحدة واحدة لمعرفة أيّها يفشل) …"
  "$VENV_PY" -m pip install --quiet --upgrade pip setuptools wheel >/dev/null 2>&1 || true

  local deps="$SCRIPT_DIR/setup_deps.py"

  # المسار المشترك: المُثبِّت الواحد (يعالج Termux وعجلات TUR وpkg بنفسه)
  if [[ -f "$deps" ]]; then
    if "$VENV_PY" "$deps"; then
      ok "الحاجيات مثبّتة ومتحقَّقة بالاستيراد"
      return 0
    fi
    if [[ -n "${WISPBYTE:-}" || -d /home/container ]]; then
      die "فشل تثبيت مجموعة Wispbyte الكاملة — راجع سبب الفشل أعلاه.
   التشخيص: $VENV_PY self_install.py --check"
    fi
    die "فشل تثبيت الحاجيات — السبب مذكور أعلاه باسم الحزمة.
   لعرض التفاصيل كاملة: $VENV_PY -m pip install -r requirements.txt"
  fi

  # احتياط: لو غاب الملف (نسخة قديمة من المشروع)
  warn "لم أجد setup_deps.py — أستخدم الطريقة المباشرة"
  if (( IS_TERMUX )); then
    termux_requirements "$VENV_PY" || die "فشل تثبيت الحاجيات على أندرويد."
    ok "الحاجيات مثبّتة (نسخة أندرويد)"
    return 0
  fi

  if ! "$VENV_PY" -m pip install --quiet -r requirements.txt; then
    die "فشل تثبيت الحاجيات.
   جرّب: $VENV_PY -m pip install -r requirements.txt   (لعرض السبب كاملًا)"
  fi
  ok "الحاجيات مثبّتة"
}


# هل pip متاح؟ (يشمل Termux: pip النظام مرئي بسبب --system-site-packages،
# وsys.prefix هو البيئة ⇒ التثبيت يذهب داخل البيئة لا إلى النظام)
venv_has_pip() {
  [[ -n "${VENV_PY:-}" ]] || return 1
  "$VENV_PY" -m pip --version >/dev/null 2>&1
}


# سُلّم ٥ درجات لتجهيز pip **بلا ensurepip** — كل درجة مستقلة
ensure_venv_pip() {
  local py="$VENV_PY"

  # (١) pip النظام المرئي داخل البيئة (الحالة الشائعة على Termux)
  if "$py" -m pip --version >/dev/null 2>&1; then
    info "أستخدم pip الموجود في حزم النظام (يكتب داخل البيئة)"
    return 0
  fi

  # (٢) وحدة virtualenv: تنشئ pip من عجلاتها المدمجة
  info "محاولة virtualenv …"
  "$PYTHON" -m pip install --quiet --user virtualenv >/dev/null 2>&1 || true
  if "$PYTHON" -m virtualenv --version >/dev/null 2>&1; then
    if "$PYTHON" -m virtualenv --python "$py" "$VENV_DIR" >/dev/null 2>&1; then
      if "$py" -m pip --version >/dev/null 2>&1; then
        return 0
      fi
    fi
  fi

  # (٣) get-pip.py الرسمي
  local getpip=".get-pip.py"
  if download_file https://bootstrap.pypa.io/get-pip.py "$getpip"; then
    info "تشغيل get-pip.py …"
    "$py" "$getpip" --quiet >/dev/null 2>&1 || true
    rm -f "$getpip"
    if "$py" -m pip --version >/dev/null 2>&1; then
      return 0
    fi
  fi

  # (٤) عجلة pip من PyPI تُفكّ وتُضاف للمسار (آخر حل بلا شبكة خاصة)
  info "محاولة تنزيل عجلة pip مباشرة …"
  local wheel
  wheel="$(pip_wheel_url 2>/dev/null || true)"
  if [[ -n "$wheel" ]]; then
    mkdir -p ".pip_tmp"
    if download_file "$wheel" ".pip_tmp.whl"; then
     if "$py" -m zipfile -e ".pip_tmp.whl" ".pip_tmp" >/dev/null 2>&1; then
      if PYTHONPATH="$SCRIPT_DIR/.pip_tmp" "$py" -m pip --version >/dev/null 2>&1; then
        PYTHONPATH="$SCRIPT_DIR/.pip_tmp" "$py" -m pip install --quiet \
          --upgrade pip >/dev/null 2>&1 || true
      fi
     fi
    fi
    rm -rf ".pip_tmp" ".pip_tmp.whl"
    if "$py" -m pip --version >/dev/null 2>&1; then
      return 0
    fi
  fi

  return 1
}


# رابط أحدث عجلة pip من PyPI (بلا jq، ببايثون فقط)
pip_wheel_url() {
  local json=".pypi-pip.json"
  download_file https://pypi.org/pypi/pip/json "$json" || return 1
  "$PYTHON" - "$json" <<'PYEOF'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
for f in data.get("urls", []):
    if f.get("packagetype") == "bdist_wheel" and f["filename"].endswith("-none-any.whl"):
        print(f["url"])
        break
PYEOF
  rm -f "$json"
}


# ------------------------------------- تثبيت الحاجيات على أندرويد/Termux
#
# 🔴 سبب فشل حقيقي على الجوال (وليس تخمينًا): بايثون على أندرويد يُعرّف نفسه
#    بوسم منصّة `android_24_arm64_v8a` — و pip لا يقبل معه أي عجلة لينكس
#    (`manylinux_aarch64` أو `linux_aarch64`)؛ الوسمان غير متوافقين. وPyPI
#    **لا ينشر أي عجلة أندرويد** لأي حزمة مُجمَّعة (تحقّقنا: 0 لكل من numpy
#    وpillow وpydantic-core وgreenlet…). فكل حزمة فيها كود C/Rust:
#      • إما تُبنى من المصدر على الجوال → تحتاج Rust/clang. `python-bidi`
#        0.6 تحديدًا يبني `maturin`، وrustup يرفض ثلاثي
#        `aarch64-unknown-linux-android` أصلًا → فشل مؤكد، ويُسقِط **كل**
#        `pip install` معه (وهذا بالضبط ما ظهر في اللقطة: عند 3/6).
#      • أو تُؤخذ جاهزة من `pkg` (وهي مبنية لهذا الوسم بالذات).
#
#    الحل هنا ثلاثي: (١) نطلب من التنصيبة **عجلات فقط** (--only-binary) فلا
#    يُحاول pip بناء أي شيء أبدًا؛ (٢) نوجّه pip إلى فهرس TUR الذي ينشر عجلات
#    أندرويد حقيقية (aiohttp/numpy/pydantic-core/markupsafe/lxml…)
#    علمًا بأنه **لا ينشر** pillow/onnxruntime/asyncpg/greenlet/cryptography؛
#    (٣) ما لا عجلة له يُؤخذ من `pkg`، وما لا `pkg` له يُعلَن صراحةً ويُتخطّى.
#
#    ونفصل النوعين: حاجيات **إقلاع** (البوت لا يبدأ بدونها) وحاجيات
#    **اختيارية** (غيابها يخفض قدرة فقط). لا نُسقِط التثبيت كله لأجل اختياري.
TUR_PYPI="https://termux-user-repository.github.io/pypi/"

# حزم لا بديل لها إلا `pkg` (لا عجلة أندرويد على PyPI ولا في TUR).
# كل عنصر: اسم وحدة الاستيراد|حزمة pkg|اسم العرض العربي
termux_pkg_only() {
  local spec="$1"
  case "$spec" in
    # pillow: TUR لا ينشر cp314 لها وPyPI لا ينشر أندرويد — فـpkg هي الطريق.
    PIL)        echo "python-pillow|Pillow" ;;
    greenlet)   echo "python-greenlet|greenlet" ;;   # يحتاجها SQLAlchemy غير المتزامن
    *) return 1 ;;
  esac
}

termux_requirements() {
  local py="$1"
  local filtered=".requirements-android.txt"
  local optional_file=".requirements-android-optional.txt"
  : > "$filtered"; : > "$optional_file"

  # ── ١) ما نعرفه مسبقًا أنه لا يُبنى ولا يُوجد كعجلة → يُتخطّى بصراحة
  local unavailable=()
  local -a skip_pip=()           # تُؤخذ من pkg فلا نطلبها من pip

  # ── ٢) تثبيت حزم pkg التي لا بديل لها (وهي مبنية لهذا الوسم بالذات)
  local need_pkg=(python-pip) line name
  local spec_mod
  for spec_mod in PIL greenlet; do
    local meta; meta="$(termux_pkg_only "$spec_mod")" || continue
    need_pkg+=("${meta%%|*}")
  done
  # python-numpy: عجلة TUR موجودة، لكن حزمة pkg أسرع وأخفّ — نجرّبها أولًا
  need_pkg+=(python-numpy)

  info "تثبيت حزم أندرويد الأصلية من pkg …"
  # لا نحكم بالتوفّر من `pkg show` وحده (يفشل لأسباب فهرسة/شبكة)، ونجرّب
  # كل حزمة منفردة كي لا تُسقِط واحدة فاشلة البقية.
  local pkg_ok=() pkg_bad=()
  for name in "${need_pkg[@]}"; do
    if pkg install -y "$name" >/dev/null 2>&1; then pkg_ok+=("$name"); else pkg_bad+=("$name"); fi
  done
  ((${#pkg_ok[@]})) && ok "من pkg: ${pkg_ok[*]}"
  ((${#pkg_bad[@]})) && warn "لم تُثبَّت من pkg: ${pkg_bad[*]}"

  # ── ٣) بناء قائمة pip: كل سطر في requirements.txt إما إقلاع أو اختياري
  #     الاختياري (pytesseract/pdf2image) يُعالَج في المرحلة ٥ منفردًا.

  while IFS= read -r line; do
    case "$line" in ""|"#"*) continue ;; esac
    name="${line%%[<>=!~ ]*}"; name="${name,,}"
    case "$name" in
      onnxruntime)
        # لا عجلة أندرويد على PyPI ولا TUR، ولا حزمة pkg → يُتخطّى بصراحة
        unavailable+=("onnxruntime — فكّ التواء صور الموبايل (OCR العادي يعمل)"); continue ;;
      pillow)
        # نُثبّتها من pkg؛ نتحقّق فعلًا أنها تعمل قبل تخطّيها
        if "$py" -c "import PIL" >/dev/null 2>&1; then skip_pip+=("pillow"); continue
        else warn "Pillow غير متاحة — سأحاولها من pip كعجلة"; fi ;;
      greenlet|numpy)
        if "$py" -c "import $name" >/dev/null 2>&1; then skip_pip+=("$name"); continue; fi ;;
      python-bidi)
        # ❗الحزمة التي أسقطت التثبيت: Rust/maturin، لا عجلة أندرويد، ولا
        #   يستوردها البوت إطلاقًا (اختبار واحد فقط، وهو يتخطّى تلقائيًا).
        info "تخطّي python-bidi (Rust — اختبار فقط، البوت لا يستخدمه)"; continue ;;
    esac
    printf '%s\n' "$line" >> "$filtered"
  done < requirements.txt

  # ما أخذناه من pkg: نؤكّد بالاستيراد الحقيقي قبل تخطّيه في pip
  for name in "${skip_pip[@]}"; do
    info "تخطّي $name (مثبّتة ومُختبَرة من pkg)"
  done

  # ── ٤) تثبيت حاجيات الإقلاع: **عجلات فقط** + فهرس TUR
  info "تثبيت حاجيات البوت من عجلات أندرويد جاهزة (بلا أي بناء) …"
  local pip_base=(-m pip install --quiet --no-input --no-cache-dir
                  --only-binary=:all: --extra-index-url "$TUR_PYPI")
  local pip_log=".pip-android.log"
  if ! "$py" "${pip_base[@]}" -r "$filtered" >"$pip_log" 2>&1; then
    warn "تعذّر تثبيت القائمة كاملة — سأجرّب كل حزمة منفردة لأعرف أيّها بالضبط"
    local failed=()
    while IFS= read -r line; do
      case "$line" in ""|"#"*) continue ;; esac
      "$py" "${pip_base[@]}" "$line" >>"$pip_log" 2>&1 || failed+=("${line%%[<>=!~ ]*}")
    done < "$filtered"
    if ((${#failed[@]})); then
      # نُخرج **سبب** الفشل الحقيقي لا كلمة «فشل» فقط
      warn "لم تُثبَّت: ${failed[*]}"
      local why; why="$(grep -iE "error|not supported|no matching|failed" "$pip_log" | tail -3)"
      [[ -n "$why" ]] && { info "سبب pip:"; printf '%s\n' "$why" | while IFS= read -r l; do info "  $l"; done; }
    fi
  else
    ok "ثُبّتت حاجيات الإقلاع كعجلات جاهزة"
  fi

  # ── ٥) الاختياري: لا يُسقِط التثبيت. نجرّبه واحدًا واحدًا ونتجاهل الفشل.
  local opt
  for opt in pytesseract pdf2image; do
    "$py" -c "import $opt" >/dev/null 2>&1 && continue
    if "$py" -m pip install --quiet --no-input --no-cache-dir --only-binary=:all: "$opt" >>"$pip_log" 2>&1; then
      :
    else
      unavailable+=("$opt — ميزة ناقصة، ليست ضرورية للإقلاع")
    fi
  done
  "$py" -c "import numpy" >/dev/null 2>&1 || unavailable+=("numpy")

  # ── ٦) تحقّق فعلي بالاستيراد: «نجح التثبيت» ≠ «يعمل»
  local required_imports=(aiogram:aiogram sqlalchemy:SQLAlchemy aiosqlite:aiosqlite
                          pypdf:pypdf httpx:httpx alembic:alembic openpyxl:openpyxl
                          numpy:numpy greenlet:greenlet)
  local missing=() mod
  for line in "${required_imports[@]}"; do
    mod="${line%%:*}"
    "$py" -c "import $mod" >/dev/null 2>&1 || missing+=("${line##*:}")
  done
  # Pillow: رسالة واضحة بدل انهيار غامض لاحقًا
  "$py" -c "import PIL" >/dev/null 2>&1 || missing+=("Pillow")

  if ((${#missing[@]})); then
    warn "حزم يحتاجها البوت غير موجودة: ${missing[*]}"
    info "محاولة أخيرة: إعادة تثبيت الناقص واحدة واحدة (بالعجلات فقط) …"
    local pkg_name_fix req_line
    for pkg_name_fix in "${missing[@]}"; do
      req_line="$(grep -iE "^${pkg_name_fix}([<>=!~ ]|$)" requirements.txt | head -1 || true)"
      [[ -n "$req_line" ]] || req_line="$pkg_name_fix"
      "$py" "${pip_base[@]}" "$req_line" >>"$pip_log" 2>&1 || true
    done
    # إعادة الفحص: لا نكتفي بمحاولة التثبيت — نتحقّق من الاستيراد فعلًا
    local still=()
    for line in "${required_imports[@]}"; do
      mod="${line%%:*}"
      "$py" -c "import $mod" >/dev/null 2>&1 || still+=("${line##*:}")
    done
    "$py" -c "import PIL" >/dev/null 2>&1 || still+=("Pillow")
    if ((${#still[@]})); then
      warn "ما زالت ناقصة: ${still[*]}"
      info "البوت لن يبدأ بدونها. لمعرفة السبب الكامل:"
      info "  $py -m pip install ${still[*]}"
      info "أندرويد: تأكد من الحزم الأصلية: pkg install python-pillow python-greenlet python-numpy"
      local why2; why2="$(grep -iE "error|not supported|no matching" "$pip_log" | tail -3)"
      [[ -n "$why2" ]] && { info "سبب pip:"; printf '%s\n' "$why2" | while IFS= read -r l; do info "  $l"; done; }
      return 1
    fi
    ok "ثُبّتت الحزم الناقصة بعد إعادة المحاولة"
  fi
  ok "كل حزم الإقلاع موجودة ومُختبَرة بالاستيراد"

  # ── ٧) تقرير صريح بالناقص الاختياري (لا وعود كاذبة)
  if ((${#unavailable[@]})); then
    warn "غير متوفّر على أندرويد (البوت يعمل بدونه):"
    local u; for u in "${unavailable[@]}"; do info "$u"; done
  fi
  rm -f "$filtered" "$optional_file"
  return 0
}

# ------------------------------------------------ 4) ملف .env
valid_token() { [[ "$1" =~ ^[0-9]{5,}:[A-Za-z0-9_-]{20,}$ ]] && [[ "$1" != *AAExample* ]] && [[ "$1" != *ReplaceMe* ]]; }
valid_id()    { [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" != "0" ]]; }
valid_channel() { [[ "$1" =~ ^-100[0-9]+$ ]] || [[ "$1" =~ ^@[A-Za-z0-9_]{4,}$ ]]; }

step_env() {
  head_ "4/6 ملف الإعدادات .env"
  if [[ -f "$ENV_FILE" ]]; then
    ok "$ENV_FILE موجود — سأحافظ على إعداداته الحالية"
    # لا نتجاوز الملف الموجود: نطلب فقط القيم الإلزامية الناقصة/المثالّية.
    local missing=() key value token owner channel bad=0
    for key in BOT_TOKEN OWNER_ID STORAGE_CHANNEL; do
      value="${!key:-$(grep -m1 -E "^${key}=" "$ENV_FILE" | cut -d= -f2- || true)}"
      [[ -n "$value" ]] || missing+=("$key")
      [[ "$key:$value" == BOT_TOKEN:*AAExample* || "$key:$value" == BOT_TOKEN:*ReplaceMe* || "$key:$value" == OWNER_ID:123456789 || "$key:$value" == STORAGE_CHANNEL:-1001234567890 ]] && missing+=("$key")
    done
    if [[ "${NO_INPUT:-0}" != "1" && ( -t 0 || "${ZAKR_INTERACTIVE_SETUP:-0}" == "1" ) && ${#missing[@]} -gt 0 ]]; then
      printf '\nإعدادات ناقصة في .env — أدخل المطلوب فقط (Enter للتخطي):\n'
      token="${BOT_TOKEN:-$(grep -m1 '^BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)}"
      owner="${OWNER_ID:-$(grep -m1 '^OWNER_ID=' "$ENV_FILE" | cut -d= -f2- || true)}"
      channel="${STORAGE_CHANNEL:-$(grep -m1 '^STORAGE_CHANNEL=' "$ENV_FILE" | cut -d= -f2- || true)}"
      if [[ -z "$token" || "$token" == *AAExample* || "$token" == *ReplaceMe* ]]; then
        read -r -s -p 'توكن البوت من @BotFather: ' token || true; printf '\n'
        token="${token//[$'\\n\\r\\t ']/}"
        [[ -z "$token" ]] || { valid_token "$token" && _set_env BOT_TOKEN "$token" || { warn 'صيغة BOT_TOKEN غير صحيحة'; bad=1; }; }
      fi
      if [[ -z "$owner" || "$owner" == '123456789' ]]; then
        read -r -p 'رقمك في تيليجرام (OWNER_ID): ' owner || true
        owner="${owner//[$'\\n\\r\\t ']/}"
        [[ -z "$owner" ]] || { valid_id "$owner" && _set_env OWNER_ID "$owner" || { warn 'OWNER_ID يجب أن يكون أرقامًا فقط'; bad=1; }; }
      fi
      if [[ -z "$channel" || "$channel" == '-1001234567890' ]]; then
        read -r -p 'آيدي قناة التخزين (STORAGE_CHANNEL): ' channel || true
        channel="${channel//[$'\\n\\r\\t ']/}"
        [[ -z "$channel" ]] || { valid_channel "$channel" && _set_env STORAGE_CHANNEL "$channel" || { warn 'صيغة STORAGE_CHANNEL غير صحيحة'; bad=1; }; }
      fi
      ((bad == 0)) && ok 'تم تحديث .env مع الحفاظ على بقية الإعدادات'
    elif ((${#missing[@]})); then
      warn "إعدادات ناقصة في $ENV_FILE: ${missing[*]}"
      info 'شغّل setup من طرفية تفاعلية لإدخالها، أو أضفها إلى Variables في لوحة الاستضافة.'
    fi
    return 0
  fi

  [[ -f "$ENV_EXAMPLE" ]] || die "$ENV_EXAMPLE مفقود — لا أستطيع إنشاء $ENV_FILE"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  ok "أُنشئ $ENV_FILE من $ENV_EXAMPLE"

  if [[ "${NO_INPUT:-0}" != "1" ]]; then
    printf '\nسأسألك عن 3 قيم فقط (Enter = تخطّي واملأها لاحقًا):\n\n'
    info "1) التوكن من @BotFather  2) رقمك من @userinfobot  3) آيدي قناة التخزين"
  else
    info "وضع آلي (بلا أسئلة): القيم من متغيّرات البيئة"
  fi

  # ⚠️ أمان: في وضع لوحة الاستضافة/الآلي لا نكتب الأسرار في .env إطلاقًا.
  #    السبب: اللوحة تحفظها كمتغيّرات، وكتابتها في ملف تُكرّر السرّ على
  #    القرص وتظهر لأي شخص يفتح مدير الملفات أو يُنزّل نسخة احتياطية.
  #    البوت يقرأ متغيّرات البيئة أولًا (.env لا يفوز عليها)، فالمتغيّرات كافية.
  if [[ "${NO_INPUT:-0}" == "1" ]]; then
    info ".env يبقى نموذجًا — الأسرار تُقرأ من متغيّرات البيئة (لا تُكتب في ملف)"
    return 0
  fi

  # الأولوية: خيارات السكربت ⇒ متغيّرات البيئة ⇒ السؤال.
  # (متغيّرات البيئة أفضل من الخيارات لأن الخيارات تظهر في `ps`.)
  local token owner channel
  token="${OPT_TOKEN:-${BOT_TOKEN_SRC:-}}"
  owner="${OPT_OWNER:-${OWNER_ID_SRC:-}}"
  channel="${OPT_CHANNEL:-${STORAGE_CHANNEL_SRC:-}}"

  [[ -n "$token" ]]   || token="$(ask "توكن البوت (BOT_TOKEN)")"
  [[ -n "$owner" ]]   || owner="$(ask "رقمك في تيليجرام (OWNER_ID)")"
  [[ -n "$channel" ]] || channel="$(ask "آيدي قناة التخزين (STORAGE_CHANNEL)")"

  # لصق فيه أسطر أو مسافات أو علامات تنصيص شائع جدًا
  token="${token//[$'\n\r\t ']/}";  token="${token//\"/}"
  owner="${owner//[$'\n\r\t ']/}"
  channel="${channel//[$'\n\r\t ']/}"; channel="${channel//\"/}"

  local bad=0
  if [[ -n "$token" ]]; then
    if valid_token "$token"; then
      _set_env BOT_TOKEN "$token"
    else
      warn "التوكن لا يبدو صحيحًا (لازم يحتوي ':' وطوله 20+) — تركته كما هو"
      bad=1
    fi
  fi
  if [[ -n "$owner" ]]; then
    if valid_id "$owner"; then _set_env OWNER_ID "$owner"
    else warn "OWNER_ID لازم يكون أرقامًا فقط — تركته كما هو"; bad=1; fi
  fi
  if [[ -n "$channel" ]]; then
    if valid_channel "$channel"; then _set_env STORAGE_CHANNEL "$channel"
    else
      warn "آيدي القناة يبدأ بـ -100 (خاصة) أو @اسم (عامة) — تركته كما هو"
      bad=1
    fi
  fi

  if ((bad)); then
    warn "راجع القيم غير الصحيحة في $ENV_FILE قبل التشغيل"
  else
    ok "حُفظت القيم في $ENV_FILE (صلاحية الملف 600)"
  fi
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

_set_env() {  # _set_env KEY VALUE — يستبدل السطر أو يضيفه، بلا كشف القيمة
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  # نُهرّب الرموز الخاصة لـ sed حتى لا تتعطّل قيمة فيها / أو &
  local escaped
  escaped="$(printf '%s' "$value" | sed -e 's/[\/&|]/\\&/g')"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE" > "$tmp"
  else
    { cat "$ENV_FILE"; printf '%s=%s\n' "$key" "$value"; } > "$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  export "$key=$value"
}

# ------------------------------------------------ 5) فحص ذاتي (بلا شبكة)
step_selfcheck() {
  head_ "5/6 فحص ذاتي (بلا اتصال بتيليجرام)"
  if "$VENV_PY" - <<'PY'
import os, sys
sys.path.insert(0, os.getcwd())
try:
    from bot.config import settings
except Exception as exc:                       # إعدادات/استيراد مكسور
    print(f"✖ تعذّر تحميل الإعدادات: {type(exc).__name__}: {exc}")
    sys.exit(2)

problems = []
# قيم .env.example المثالّية — يجب كشفها مبكرًا وإلا فشل البوت على السيرفر
PLACEHOLDER = {
    "BOT_TOKEN": ("123456789:AAExample", "AAExample", "ReplaceMe"),
    "OWNER_ID": ("123456789",),
    "STORAGE_CHANNEL": ("-1001234567890",),
}
_vals = {
    "BOT_TOKEN": settings.bot_token,
    "OWNER_ID": str(settings.owner_id or ""),
    "STORAGE_CHANNEL": settings.storage_channel,
}
for _k, _markers in PLACEHOLDER.items():
    if any(m in (_vals[_k] or "") for m in _markers):
        problems.append(f"{_k} لا يزال قيمة مثالّية من .env.example — غيّره")
if not settings.bot_token:
    problems.append("BOT_TOKEN غير مُعدّ")
if not settings.owner_id:
    problems.append("OWNER_ID غير مُعدّ")
if not settings.storage_channel:
    problems.append("STORAGE_CHANNEL غير مُعدّ")

# بناء المخطط في قاعدة مؤقتة: يثبت أن النماذج سليمة وأن كل جدول قابل للإنشاء
import asyncio
from pathlib import Path
tmp = Path("./data/.selfcheck.db")
tmp.parent.mkdir(parents=True, exist_ok=True)
if tmp.exists():
    tmp.unlink()

async def check_db() -> None:
    from bot.database import ensure_schema, init_engine
    eng = init_engine(f"sqlite+aiosqlite:///{tmp}")
    await ensure_schema(eng)
    await eng.dispose()

try:
    asyncio.run(check_db())
except Exception as exc:
    problems.append(f"تعذّر بناء قاعدة البيانات: {type(exc).__name__}: {exc}")
finally:
    tmp.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(tmp) + suffix).unlink(missing_ok=True)

import bot.main as m   # noqa: F401  — استيراد كل المعالجات يكشف أخطاء الربط

# ① الموجّهات الجديدة مسجّلة فعلًا (زر لا يستجيب = عطل صامت)
_router_names = {getattr(r, "name", "") for r in m.ROUTER_ORDER}
for _needed in ("materials", "explain_material"):
    if _needed not in _router_names:
        problems.append(f"الموجّه «{_needed}» غير مسجَّل في ROUTER_ORDER")

# ② حفظ رموز برومبت المستخدم حرفيًا — فحص سلوكي لا وجودي
try:
    from bot.services import verbatim as _v
    _sample = "V = I × R حيث R > 0"
    _p = _v.protect(_sample)
    if not _p.saved:
        problems.append("حماية رموز البرومبت لا تحفظ شيئًا")
    elif _p.restore(_p.text, for_html=False) != _sample:
        problems.append("حماية الرموز لا تُعيد النص كما هو — الشرح سيفقد الرموز")
    else:
        print(f"✔ حفظ الرموز حرفيًا يعمل ({len(_p.saved)} رموز في الاختبار)")
except Exception as exc:                       # noqa: BLE001
    problems.append(f"فحص حفظ الرموز فشل: {type(exc).__name__}")

# على أندرويد: نتأكد أن الحزم القابلة للتثبيت فعلًا مستوردة، وأن الناقص
# المعروف (onnxruntime) غير مطلوب عند الإقلاع — لا نُفشل التثبيت بسببه.
missing_optional = []
for name, label in (("numpy", "numpy"), ("PIL", "Pillow")):
    try:
        __import__(name)
    except Exception:
        missing_optional.append(label)
if missing_optional:
    problems.append("حزم ناقصة: " + "، ".join(missing_optional)
                    + " — جرّب: pkg install python-numpy python-pillow")

print("MODE:", settings.ai_provider, "| AI:", "مفتاح موجود" if settings.ai_provider_keys or settings.ai_api_key else "بلا مفتاح (طبيعي)")
if problems:
    for p in problems:
        print(f"⚠ {p}")
    sys.exit(1)
print("✔ كل الفحوص نجحت")
PY
  then
    ok "الإعدادات والمخطط سليمان"
  else
    local rc=$?
    if [[ $rc -eq 2 ]]; then
      die "تعذّر تحميل الإعدادات — راجع $ENV_FILE"
    fi
    warn "الفحص الذاتي وجد ملاحظات (أعلاه). البوت قد يعمل، لكن راجع الأسطر الناقصة."
  fi
}

# ------------------------------------------------ 6) التشغيل
run_local() {
  head_ "تشغيل البوت"
  info "Ctrl+C للإيقاف"
  ZAKR_SETUP_COMPLETE=1 PYTHONUNBUFFERED=1 exec "$VENV_PY" -u "$SCRIPT_DIR/main.py"
}

run_tests() {
  head_ "تشغيل الاختبارات"
  if [[ ! -d tests ]]; then
    die "مجلد tests غير موجود — فكّ zakr-bot-tests.zip داخل مجلد المشروع أولًا"
  fi
  "$VENV_PY" -m pytest -q
}

run_docker() {
  head_ "التشغيل عبر دوكر"
  have docker || die "docker غير مثبّت: https://docs.docker.com/engine/install/"
  [[ -f "$ENV_FILE" ]] || die "اعمل $ENV_FILE أولًا (شغّل السكربت بدون --docker)"
  mkdir -p data backups
  info "بناء الصورة $DOCKER_IMAGE …"
  docker build -t "$DOCKER_IMAGE" .
  if docker ps -a --format '{{.Names}}' | grep -qx "$SERVICE_NAME"; then
    warn "حاوية باسم $SERVICE_NAME موجودة — سأعيد تشغيلها بالصورة الجديدة"
    docker rm -f "$SERVICE_NAME" >/dev/null
  fi
  docker run -d --name "$SERVICE_NAME" --restart unless-stopped \
    --env-file "$ENV_FILE" \
    -v "$(pwd)/data:/app/data" -v "$(pwd)/backups:/app/backups" \
    "$DOCKER_IMAGE"
  ok "يعمل الآن في الخلفية"
  info "السجل: docker logs -f $SERVICE_NAME"
  info "الإيقاف: docker stop $SERVICE_NAME"
}

run_systemd() {
  head_ "تثبيت خدمة systemd"
  have systemctl || die "systemd غير متوفّر في هذا النظام"
  local unit="/etc/systemd/system/${SERVICE_NAME}.service"
  local run_user="${SUDO_USER:-$(id -un)}"
  local body
  body="[Unit]
Description=Zakr Study Bot
After=network-online.target

[Service]
Type=simple
User=${run_user}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/${VENV_DIR}/bin/python -u -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"

  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    have sudo || die "تحتاج صلاحية جذر (sudo) لكتابة $unit"
    printf '%s\n' "$body" | sudo tee "$unit" >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$SERVICE_NAME"
    ok "الخدمة تعمل الآن — وتشتغل تلقائيًا بعد أي إعادة تشغيل"
    info "الحالة:   sudo systemctl status $SERVICE_NAME"
    info "السجل:    sudo journalctl -u $SERVICE_NAME -f"
    info "الإيقاف:  sudo systemctl stop $SERVICE_NAME"
  else
    printf '%s\n' "$body" > "$unit"
    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"
    ok "الخدمة تعمل الآن"
  fi
}

run_check() {
  head_ "فحص البيئة (بلا تثبيت)"
  if (( IS_TERMUX )); then
    ok "المنصّة: أندرويد / Termux (${PREFIX:-?})"
  else
    info "المنصّة: $(uname -s 2>/dev/null || echo linux)"
  fi
  local py
  if py="$(python_ok)"; then ok "بايثون: $("$py" --version 2>&1)"; else warn "بايثون 3.$PY_MIN_MINOR+ غير موجود"; fi
  if have tesseract; then ok "tesseract: $(tesseract --version 2>&1 | head -1)"; else warn "tesseract غير مثبّت"; fi
  if have pdftoppm; then ok "poppler (pdftoppm) موجود"; else warn "poppler غير مثبّت"; fi
  if [[ -d "$VENV_DIR" ]]; then
    if venv_is_valid; then
      ok "البيئة الافتراضية سليمة ($VENV_DIR)"
    else
      warn "مجلد $VENV_DIR موجود لكن بايثون داخله ناقص/مكسور"
      info "  أصلحه بلا حذف يدوي: bash setup.sh   (سيعيد بناءه تلقائيًا)"
    fi
  else
    warn "لا بيئة افتراضية (شغّل السكربت بلا --check)"
  fi
  if [[ -f "$ENV_FILE" ]]; then
    ok "$ENV_FILE موجود"
    for key in BOT_TOKEN OWNER_ID STORAGE_CHANNEL; do
      if grep -qE "^${key}=.+$" "$ENV_FILE"; then info "  $key ✔"; else warn "  $key ناقص"; fi
    done
  else
    warn "$ENV_FILE غير موجود"
  fi
  if (( IS_TERMUX )); then
    if ls "${PREFIX:-/data/data/com.termux/files/usr}"/share/tessdata/ara* >/dev/null 2>&1; then
      ok "بيانات اللغة العربية (ara) موجودة"
    else
      warn "بيانات ara ناقصة — سينزّلها السكربت، أو: "
      info "  curl -L -o \$PREFIX/share/tessdata/ara.traineddata \\"
      info "    https://github.com/tesseract-ocr/tessdata_fast/raw/main/ara.traineddata"
    fi
    info "فكّ التواء الصور (onnxruntime) غير متاح على أندرويد — طبيعي"
  else
    if have docker; then ok "docker: $(docker --version 2>&1 | head -1)"; else info "docker غير مثبّت (اختياري)"; fi
  fi
}

usage() {
  # نطبع رأس التعليقات فقط (من السطر 2 حتى أول سطر لا يبدأ بـ #)
  awk 'NR > 1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
  exit 0
}

# تحقق **مبكر** من متغيّرات اللوحة قبل أي عمل — لا نُنشئ .env ثم نكتشف
# أن القيم ناقصة. على Wispbyte لا توجد طرفية، فالرسالة يجب أن تكون واضحة
# ومكتفية بذاتها في سجل اللوحة.
# تنزيل tesseract محليًا على لوحات الاستضافة (بلا root وبلا apt install).
# `apt-get download` لا يحتاج صلاحية root — ننزّل الـ.deb ونفكّه في .ocr/
# ثم نشغّله بـ LD_LIBRARY_PATH. البوت يكتشفه تلقائيًا (bot/services/ocr.py).
panel_ocr_fetch() {
  [[ "${ZAKR_SKIP_OCR:-0}" == "1" ]] && { info "تخطّي OCR (ZAKR_SKIP_OCR=1)"; return 0; }

  # يعمل أصلًا؟ (سيسأل البوت نفسه لا نخمّن)
  if "$VENV_PY" -c "import sys; sys.path.insert(0,'.'); from bot.services import ocr; sys.exit(0 if ocr.is_available() else 1)" 2>/dev/null; then
    ok "قارئ الملفات المصوّرة يعمل"
    return 0
  fi

  [[ -f "$SCRIPT_DIR/ocr_fetch.py" ]] || { info "ocr_fetch.py غير موجود"; return 0; }

  info "تنزيل tesseract محليًا (بلا root) …"
  if "$VENV_PY" "$SCRIPT_DIR/ocr_fetch.py"; then
    ok "قارئ الملفات المصوّرة جاهز"
  else
    warn "تعذّر تنزيل OCR — البوت يعمل بدونه، والرفع لا يفشل."
    info "للتخطّي دائمًا: أضف ZAKR_SKIP_OCR=1 في متغيّرات اللوحة"
  fi
  return 0
}


panel_precheck() {
  local missing=() placeholder=() key val
  for key in BOT_TOKEN OWNER_ID STORAGE_CHANNEL; do
    val="${!key:-}"
    [[ -n "$val" ]] || missing+=("$key")
  done
  # قيم .env.example المثالّية
  [[ "${BOT_TOKEN:-}" == *AAExample* || "${BOT_TOKEN:-}" == *ReplaceMe* ]] && placeholder+=("BOT_TOKEN")
  [[ "${OWNER_ID:-}" == "123456789" ]] && placeholder+=("OWNER_ID")
  [[ "${STORAGE_CHANNEL:-}" == "-1001234567890" ]] && placeholder+=("STORAGE_CHANNEL")

  if ((${#missing[@]} == 0 && ${#placeholder[@]} == 0)); then
    return 0
  fi

  printf '\n%s✖ إعدادات اللوحة ناقصة — البوت لن يبدأ%s\n' "$C_ERR" "$C_RESET" >&2
  ((${#missing[@]}))    && printf '   مفقود: %s\n' "${missing[*]}" >&2
  ((${#placeholder[@]})) && printf '   قيمة مثالّية (من .env.example): %s\n' "${placeholder[*]}" >&2
  cat >&2 <<'EOH'

   أضفها في لوحة Wispbyte:
     تبويب  Startup  ←  Variables  ←  أضف متغيّرًا لكل واحد:

        BOT_TOKEN         التوكن من @BotFather
        OWNER_ID          رقمك من @userinfobot
        STORAGE_CHANNEL   آيدي قناتك (يبدأ بـ -100)

   احفظ (Save) ثم أعد تشغيل السيرفر (Restart).

EOH
  return 1
}


# ------------------------------------------------ التدفّق الرئيسي
MODE="start"
OPT_TOKEN=""; OPT_OWNER=""; OPT_CHANNEL=""
# قيم من البيئة (الطريقة الآمنة: لا تظهر في ps ولا في سجل الأوامر)
BOT_TOKEN_SRC="${ZAKR_BOT_TOKEN:-${BOT_TOKEN:-}}"
OWNER_ID_SRC="${ZAKR_OWNER_ID:-${OWNER_ID:-}}"
STORAGE_CHANNEL_SRC="${ZAKR_STORAGE_CHANNEL:-${STORAGE_CHANNEL:-}}"
for arg in "$@"; do
  case "$arg" in
    --start)     MODE="start" ;;
    --no-start)  MODE="install" ;;   # نظير setup.py --no-start
    --test)      MODE="test" ;;
    --docker)    MODE="docker" ;;
    --systemd)   MODE="systemd" ;;
    --check)     MODE="check" ;;
    --ocr-only)  MODE="ocr" ;;
    --token=*)   OPT_TOKEN="${arg#*=}" ;;
    --owner=*)   OPT_OWNER="${arg#*=}" ;;
    --channel=*) OPT_CHANNEL="${arg#*=}" ;;
    --wispbyte)  MODE="wispbyte" ;;
    --no-input)  NO_INPUT=1; export NO_INPUT ;;
    -h|--help)   usage ;;
    *) die "خيار غير معروف: $arg   (استخدم --help)" ;;
  esac
done

# على لوحات الاستضافة، التشغيل الافتراضي يعني إعداد Wispbyte الكامل ثم تشغيل البوت.
# لا نسمح بالمسار المحلي الاختياري للـOCR على الحاويات.
if (( IS_PANEL )) && [[ "$MODE" == "start" ]]; then
  MODE="wispbyte"
fi

printf '%s\n' "${C_HEAD}╔══════════════════════════════════════════╗${C_RESET}"
printf '%s\n' "${C_HEAD}║   بوت ذاكر — سكربت التشغيل الكامل        ║${C_RESET}"
printf '%s\n' "${C_HEAD}╚══════════════════════════════════════════╝${C_RESET}"

if (( IS_PANEL )); then
  case "$MODE" in
    docker)
      die "دوكر غير متاح على لوحة استضافة (لا صلاحية ولا daemon)." ;;
    systemd)
      die "systemd غير موجود في لوحة الاستضافة — اللوحة تدير العمليّة.
   استخدم: bash setup.sh --wispbyte" ;;
  esac
fi

if (( IS_TERMUX )); then
  case "$MODE" in
    docker)
      die "دوكر غير متاح داخل Termux (أندرويد لا يشغّل حاويات).
   استخدم: bash setup.sh --start" ;;
    systemd)
      die "systemd غير متاح على أندرويد.
   للتشغيل الدائم على الجوال: bash setup.sh --start مع تفعيل قفل الاستيقاظ:
     termux-wake-lock && bash setup.sh --start" ;;
  esac
fi

case "$MODE" in
  check)   run_check; exit 0 ;;
  *)       acquire_lock ;;
esac

case "$MODE" in
  ocr)
    # --ocr-only: فحص/تثبيت قارئ الملفات المصوّرة فقط (بلا بيئة ولا .env)
    step_ocr
    # نتيجة صريحة بكود خروج: 0 = كل شيء يعمل فعلًا
    if ocr_verify >/dev/null 2>&1; then
      ok "الفحص: tesseract + poppler + العربية تعمل كلها"
      ocr_probe_cleanup; exit 0
    else
      ocr_probe_cleanup
      die "قارئ الملفات المصوّرة غير جاهز — راجع الرسائل أعلاه"
    fi ;;
  wispbyte)
    # وضع اللوحة: اقرأ Variables أولًا، لكن اسمح بالإدخال من Console إذا كانت تفاعلية.
    info "وضع لوحة الاستضافة (Wispbyte/Pterodactyl) — Variables أو إدخال Console"
    # توحيد الأسماء البديلة التي تستخدمها بعض لوحات الاستضافة.
    [[ -n "${BOT_TOKEN:-}" ]] || BOT_TOKEN="${ZAKR_BOT_TOKEN:-}"
    [[ -n "${OWNER_ID:-}" ]] || OWNER_ID="${ZAKR_OWNER_ID:-}"
    [[ -n "${STORAGE_CHANNEL:-}" ]] || STORAGE_CHANNEL="${ZAKR_STORAGE_CHANNEL:-}"
    export BOT_TOKEN OWNER_ID STORAGE_CHANNEL
    if [[ ! -t 0 && "${ZAKR_INTERACTIVE_SETUP:-0}" != "1" ]]; then
      NO_INPUT=1; export NO_INPUT
    fi

    step_python
    step_venv
    step_env          # يقرأ .env إن وُجد، لكن متغيّرات اللوحة تفوز
    # المسار الموحّد: self_install.py يثبّت حزم بايثون+النظام ويتحقّق بالاستيراد.
    # لو غاب (نسخة قديمة) نرجع إلى panel_ocr_fetch كاحتياط.
    if [[ -f "$SCRIPT_DIR/self_install.py" ]]; then
      ZAKR_WISPBYTE_MODE=1 ZAKR_INSTALL_OCR=1 "$VENV_PY" "$SCRIPT_DIR/self_install.py" \
        || die "فشل التجهيز الإجباري على Wispbyte — لن يبدأ البوت بنواقص."
    else
      die "self_install.py مفقود — لا يمكن تثبيت كل الاعتماديات المطلوبة."
    fi
    step_selfcheck    # يفشل (set -e) لو الإعدادات غير سليمة

    ok "الإعداد اكتمل — تشغيل البوت …"
    ZAKR_SETUP_COMPLETE=1 PYTHONUNBUFFERED=1 exec "$VENV_PY" -u "$SCRIPT_DIR/main.py" ;;
  docker)  step_python; step_env; run_docker; exit 0 ;;
  systemd) step_python; step_venv; step_env; step_selfcheck; run_systemd; exit 0 ;;
esac

step_python
step_ocr
step_venv
step_env
step_selfcheck

case "$MODE" in
  test) run_tests ;;
  start) run_local ;;
  *)
    cat <<EOF

${C_OK}✔ التثبيت اكتمل${C_RESET}

التشغيل الآن:
   ./setup.sh --start          ${C_DIM}# أو:${C_RESET} bash setup.sh --start

دايمًا في الخلفية:
   ./setup.sh --systemd        ${C_DIM}# خدمة systemd (يسألك sudo)${C_RESET}
   ./setup.sh --docker         ${C_DIM}# دوكر${C_RESET}

اختبارات المشروع (تحتاج مجلد tests):
   ./setup.sh --test

بعد التشغيل جرّب في تيليجرام: /start ثم /admin
EOF
    if (( IS_TERMUX )); then
      cat <<EOF

${C_WARN}على أندرويد — حاجتان مهمتان:${C_RESET}
   1) امنع النظام من إيقاف Termux:
        termux-wake-lock
   2) البوت يقف لما تقتل التطبيق — سيبه في الخلفية (إشعار Termux ظاهر).
      للتشغيل بعد قفل الشاشة: إعدادات أندرويد ← التطبيقات ← Termux ←
      البطارية ← «غير مقيّد / Unrestricted».

   ومن تطبيق Termux نفسه: الإعدادات ← «Acquire wakelock» فعّلها.
EOF
    fi
    ;;
esac
