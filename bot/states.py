"""حالات FSM (آلة الحالة) لتدفّقات البوت متعددة الخطوات."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class UploadFlow(StatesGroup):
    """رفع ملف: مادة ← فئة ← الملف ← الوصف ← الوسوم."""

    choosing_subject = State()
    choosing_category = State()
    waiting_file = State()
    waiting_description = State()
    waiting_tags = State()


class SearchFlow(StatesGroup):
    waiting_query = State()
    waiting_subject_query = State()  # بحث داخل مادة معيّنة


class TranslateFlow(StatesGroup):
    waiting_text = State()


class SummarizeFlow(StatesGroup):
    waiting_pdf = State()


class TutorFlow(StatesGroup):
    """الشرح والتلخيص الذكي: موضوع حر أو نص/PDF للتلخيص."""

    waiting_topic = State()
    waiting_text = State()
    waiting_pdf = State()


class AiSetupFlow(StatesGroup):
    """تخصيص الذكاء الاصطناعي: مفتاح، موديل، ملاحظات."""

    waiting_api_key = State()
    waiting_model = State()
    waiting_notes = State()
    waiting_env_value = State()
    # إضافة وكيل مخصّص: الاسم ⇒ العنوان ⇒ المفتاح (خطوة بخطوة، وكل خطوة تُتحقَّق)
    waiting_agent_name = State()
    waiting_agent_url = State()
    waiting_agent_key = State()
    waiting_agent_model = State()
    # إدارة المفاتيح: تحديث قيمة مفتاح موجود، أو تغيير اسمه
    waiting_key_value = State()
    waiting_key_rename = State()


class ExamFlow(StatesGroup):
    """امتحان مولّد: من ملف، أو إجابة سؤالًا بسؤال."""

    waiting_pdf = State()
    answering = State()


class AdminFlow(StatesGroup):
    waiting_subject_name = State()
    waiting_department_name = State()
    waiting_setting_value = State()
    waiting_quiz_question = State()
    waiting_quiz_options = State()
    waiting_quiz_answer = State()


class QuizFlow(StatesGroup):
    answering = State()


class EditFlow(StatesGroup):
    """تحرير مادة يملكها المستخدم: قيمة نصية، أو اختيار من قائمة."""

    waiting_value = State()
    waiting_pick = State()


class MaterialExplainFlow(StatesGroup):
    """شرح مادة: اختيار الملف ← الاختيارات ← البرومبت الحر ← الشرح."""

    waiting_file = State()      # أرسل PDF أو اختر من مكتبتك
    choosing_mode = State()     # نوع الشرح
    choosing_depth = State()    # مستوى التفصيل
    choosing_language = State() # لغة الرد
    waiting_prompt = State()    # برومبت حر (اختياري لكن يُحفظ حرفيًا)
