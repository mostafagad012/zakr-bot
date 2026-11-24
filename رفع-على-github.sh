#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  رفع «ذاكر» إلى GitHub — سكربت واحد يجهّز كل شيء
#
#  الاستخدام (من داخل مجلد المشروع المفكوك):
#      bash رفع-على-github.sh
#
#  السكربت:
#    ① يهيّئ مستودع git لو لم يكن موجودًا (الأرشيف لا يحوي .git)
#    ② يفحص الأمان (لا .env، لا توكنات، لا مفاتيح)
#    ③ يزامن .gitignore
#    ④ يرفع — بـ gh إن توفّر، وإلا git + توكن
#
#  لا يخزّن التوكن في أي ملف.
# ═══════════════════════════════════════════════════════════════════
set -Eeuo pipefail

# ── مساعدة صريحة: بلا هذا كان `--help` يُهمَل تمامًا فيبدأ السكربت
#  تهيئة git ورفعًا فعليًا — أي أن طلب المساعدة = رفع للمستودع.
case "${1:-}" in
  -h|--help|help)
    cat <<'USAGE'
الاستخدام:
    bash رفع-على-github.sh              رفع المشروع إلى GitHub (تفاعلي)
    bash رفع-على-github.sh --check      فحص أمني فقط، بلا رفع
    bash رفع-على-github.sh --help       هذه المساعدة

ما يفعله:
  ① يهيّئ مستودع git إن لم يكن موجودًا
  ② يفحص الأمان: لا .env، لا توكنات، لا مفاتيح، لا نموذج onnx
  ③ يزامن .gitignore
  ④ يرفع بـ gh إن توفّر، وإلا git + توكن (لا يُخزَّن في أي ملف)
USAGE
    exit 0
    ;;
  --check)
    CHECK_ONLY=1
    ;;
esac

C_OK=$'\033[1;32m'; C_BAD=$'\033[1;31m'; C_WARN=$'\033[1;33m'
C_DIM=$'\033[2m'; C_B=$'\033[1m'; C_R=$'\033[0m'
ok()   { printf '%s✔%s %s\n' "$C_OK" "$C_R" "$*"; }
bad()  { printf '%s✖%s %s\n' "$C_BAD" "$C_R" "$*"; }
warn() { printf '%s⚠%s %s\n' "$C_WARN" "$C_R" "$*"; }
info() { printf '%s•%s %s\n' "$C_DIM" "$C_R" "$*"; }
say()  { printf '%s\n' "$*"; }

ask() {                       # ask "السؤال" "الافتراضي"  → يُطبع الجواب
  local prompt="$1" default="${2:-}" answer
  if [[ -n "$default" ]]; then
    read -rp "$prompt [$default]: " answer || true
    printf '%s' "${answer:-$default}"
  else
    read -rp "$prompt: " answer || true
    printf '%s' "$answer"
  fi
}

# ── 0) فحوص مسبقة ────────────────────────────────────────────────
say ""
say "════════════════════════════════════════════"
say "  رفع بوت ذاكر إلى GitHub"
say "════════════════════════════════════════════"
say ""

command -v git >/dev/null || {
  bad "git غير مثبّت"
  say "  • دبيان/أوبنتو: apt-get install git"
  say "  • أندرويد/Termux: pkg install git"
  say "  • ويندوز/ماك:    https://git-scm.com/downloads"
  exit 1
}
ok "git موجود ($(git --version | awk '{print $3}'))"

# نتأكد أننا في مجلد المشروع لا في مجلد عشوائي
if [[ ! -f main.py || ! -f bot/main.py || ! -f requirements.txt ]]; then
  bad "هذا ليس مجلد المشروع — شغّل السكربت من داخل مجلد zakr-bot"
  info "المجلد الحالي: $(pwd)"
  exit 1
fi
ok "داخل مجلد المشروع: $(pwd)"

# ── 1) تهيئة مستودع git (الأرشيف لا يحوي .git) ──────────────────
say ""
say "① تهيئة مستودع git"
if [[ -d .git ]]; then
  ok "المستودع موجود بالفعل"
else
  info "لا يوجد .git — سأهيّئه الآن"
  git init -q -b main 2>/dev/null || git init -q
fi

# ⚠️ ضمان أن الفرع اسمه main: git الأقدم من 2.28 لا يفهم -b فينشئ master،
# وGitHub الافتراضي main — فيفشل الدفع بلا سبب واضح للمستخدم.
branch="$(git branch --show-current 2>/dev/null || true)"
if [[ -z "$branch" ]]; then
  git symbolic-ref HEAD refs/heads/main      # مستودع بلا التزام بعد
  branch="main"
elif [[ "$branch" != "main" ]]; then
  warn "الفرع الحالي «$branch» — سأسمّيه main ليطابق GitHub"
  git branch -M main
  branch="main"
fi
ok "الفرع: $branch"

# هوية الالتزام: نستخدم إعدادك، وإلا نضع هوية مؤقتة للالتزام فقط
git config user.email >/dev/null 2>&1 || {
  warn "لا يوجد اسم/بريد لإعداد git — سأضع قيمة مؤقتة (يمكنك تغييرها لاحقًا)"
  git config user.name  "Zakr Bot"
  git config user.email "dev@zakr.local"
}

# ── 2) فحص أمني قبل أي شيء ──────────────────────────────────────
say ""
say "② فحص أمني"

# .gitignore: نضمن وجود القواعد الحرجة (الأرشيف يحويها، لكن المستخدم قد يفكّ بلا إخفاء)
touch .gitignore
add_rule() { grep -qxF "$1" .gitignore 2>/dev/null || printf '%s\n' "$1" >> .gitignore; }
for rule in ".env" ".venv/" "__pycache__/" "*.pyc" ".pytest_cache/" "data/*.db" \
            "backups/*.db" ".ocr/" "*.deb" "models/*.onnx" ".setup.lock/"; do
  add_rule "$rule"
done
ok ".gitignore جاهز (يمنع .env والنموذج والكاشات)"

# نُضيف ثم نفحص ما سُجّل فعلًا — الفحص على المقصود رفعه لا على القرص
git add -A 2>/dev/null || true
staged="$(git diff --cached --name-only || true)"

leaks=0
if grep -qxE '\.env' <<<"$staged"; then
  bad ".env مُسجَّل — أوقفت الرفع لحماية أسرارك"; leaks=1
fi
if [[ -f .env ]] && grep -qxE '\.env' <<<"$staged"; then :; fi
if grep -qE 'models/.*\.onnx$' <<<"$staged"; then
  bad "نموذج onnx مُسجَّل (30 ميجا) — أزله: git rm --cached models/*.onnx"; leaks=1
fi
# توكنات تيليجرام حقيقية (نتجاهل قيم الاختبار)
if git grep --cached -nE '[0-9]{8,10}:[A-Za-z0-9_-]{35}' -- ':!tests' 2>/dev/null \
   | grep -vE 'Fake|fake|example|Example|AAFake'; then
  bad "توكن تيليجرام حقيقي في الكود — راجعه"; leaks=1
fi
# مفاتيح AI حقيقية
if git grep --cached -nE 'sk-[A-Za-z0-9_-]{24,}' -- ':!tests' 2>/dev/null \
   | grep -vE 'openai1234|example|Fake'; then
  bad "مفتاح API حقيقي في الكود — راجعه"; leaks=1
fi

if [[ "${CHECK_ONLY:-0}" == "1" ]]; then
  say ""
  if (( leaks )); then
    bad "الفحص وجد تسريبات — أصلحها قبل الرفع."
    exit 1
  fi
  ok "فحص فقط (--check) — لا تسريبات، ولم يُرفع شيء."
  exit 0
fi

if (( leaks )); then
  say ""
  bad "أوقفت الرفع. أزل السرّ ثم أعد المحاولة:"
  say "    git rm --cached .env"
  say "    git commit -m 'إزالة .env'"
  say ""
  warn "لو كان السرّ قد رُفع فعلًا: دوّره فورًا — الحذف لا يمحوه من تاريخ git."
  exit 1
fi
ok "لا أسرار في الملفات المسجّلة ($(grep -c . <<<"$staged" || echo 0) ملفًا)"

# ── 3) الالتزام ─────────────────────────────────────────────────
say ""
say "③ الالتزام"
if git rev-parse --verify HEAD >/dev/null 2>&1; then
  if [[ -z "$(git status --porcelain)" ]]; then
    ok "لا تغييرات جديدة — الالتزام الحالي كافٍ"
  else
    info "رصدت تغييرات — أُلتزمها"
    git commit -q -m "تحديث المشروع" || true
    ok "تم الالتزام"
  fi
else
  git commit -q -m "بوت ذاكر — الإصدار الأول

بوت تيليجرام عربي لإدارة المواد الدراسية وشرحها بالذكاء الاصطناعي.

- مكتبة هرمية مع بحث نصي ودلالي
- إدارة موادي: تعديل وحذف ما رفعه المستخدم (صلاحية الملكية)
- الشرح من مادة: تحليل PDF + اختيارات + برومبت حر
- حفظ حرفي لرموز برومبت المستخدم
- OCR بلا root للوحات الاستضافة
- يعمل على Linux / Termux / Docker / Wispbyte"
  ok "تم الالتزام الأول"
fi

say ""
info "الملفات في المستودع: $(git ls-files | wc -l)"

# ── 4) الرفع ────────────────────────────────────────────────────
say ""
say "④ الرفع"
say ""

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  # ── المسار الأسهل: GitHub CLI
  ok "GitHub CLI مسجّل الدخول — الطريقة الأسهل"
  say ""
  repo_name="$(ask "اسم المستودع" "zakr-bot")"
  visibility="$(ask "خاص؟ (y=خاص / n=عام)" "n")"
  if [[ "${visibility,,}" == "y" ]]; then vis_flag="--private"; else vis_flag="--public"; fi

  if git remote get-url origin >/dev/null 2>&1; then
    info "المستودع البعيد مضبوط — أدفع إليه"
    git push -u origin "$branch" || { bad "فشل الدفع"; exit 1; }
  else
    gh repo create "$repo_name" "$vis_flag" --source=. --remote=origin --push \
      || { bad "فشل إنشاء المستودع/الدفع"; exit 1; }
  fi
  say ""
  ok "تم الرفع: $(git remote get-url origin)"
  exit 0
fi

# ── المسار اليدوي: git + توكن
if ! command -v gh >/dev/null 2>&1; then
  warn "GitHub CLI غير مثبّت — سنستخدم git مع توكن."
  info "تثبيت gh اختياري لكنه يسهّل الأمر: https://cli.github.com"
  say ""
fi

say "${C_B}أنشئ توكنًا:${C_R} https://github.com/settings/tokens"
info "Fine-grained ← صلاحية «Contents: Read and write» على المستودع المطلوب"
info "أو Classic ← اختر الصلاحية «repo» فقط"
say ""

gh_user="$(ask "اسم المستخدم على GitHub")"
[[ -n "$gh_user" ]] || { bad "اسم المستخدم مطلوب"; exit 1; }
repo_name="$(ask "اسم المستودع" "zakr-bot")"

say ""
say "الصق التوكن (لن يظهر على الشاشة):"
read -rsp "التوكن: " gh_token || true; say ""
[[ -n "$gh_token" ]] || { bad "التوكن مطلوب"; exit 1; }

say ""
info "تأكد أن المستودع موجود وفارغ: https://github.com/new"
info "الاسم: ${repo_name} — بلا README ولا .gitignore"
ready="$(ask "المستودع جاهز؟ (y/n)" "y")"
[[ "${ready,,}" == "y" ]] || { warn "أُلغِي الرفع"; exit 0; }

git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${gh_user}/${repo_name}.git"

say ""
say "جارٍ الرفع…"
# نمرّر التوكن في وقت الدفع فقط — لا يُكتب في .git/config
if git -c credential.helper= \
     push -u "https://${gh_user}:${gh_token}@github.com/${gh_user}/${repo_name}.git" "$branch"; then
  say ""
  ok "تم الرفع بنجاح"
  say "   https://github.com/${gh_user}/${repo_name}"
else
  say ""
  bad "فشل الرفع — الأسباب الشائعة:"
  say "  • المستودع غير موجود      → أنشئه من https://github.com/new"
  say "  • التوكن بلا صلاحية كافية → أنشئ توكنًا بصلاحية repo/Contents"
  say "  • اسم المستخدم أو المستودع خطأ"
  say "  • التوكن منتهي أو مسحوب"
  exit 1
fi

say ""
info "التوكن لم يُحفظ في أي ملف. لأمانك: أغلِق هذه النافذة عند الانتهاء."
