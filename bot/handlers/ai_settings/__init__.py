"""شاشة الذكاء الاصطناعي — الواجهة العامة.

من هنا يُستورد كل شيء: `from bot.handlers import ai_settings`.
التفصيل في الوحدات: core · providers · keys · promote · agents · env.
"""
from .core import (  # noqa: F401
    EnvResult,
    apply_to_env,
    can_manage_ai,
    intro_text,
    main_menu,
    priority_keyboard,
    provider_detail_keyboard,
    provider_detail_text,
    provider_keyboard,
    router,
    stored_key_notice,
    switch_warning,
)

# ⚠️ ترتيب الاستيراد هو ترتيب أولوية المعالجات في aiogram — يطابق الأصل.
from . import providers, keys, promote, agents, env  # noqa: E402,F401
from .agents import (  # noqa: F401
    agents_text,
    cb_agent_delete,
    cb_agent_new,
    cb_agents,
    on_agent_key,
    on_agent_model,
    on_agent_name,
    on_agent_url,
)
from .env import (  # noqa: F401
    audit_text,
    cb_audit,
    cb_env,
    cb_env_set,
    cb_notes,
    cb_restore,
    cb_usage,
    cb_usage_reset,
    cb_usage_week,
    cmd_ai,
    current_model,
    env_state_text,
    on_env_value,
    on_notes,
    settings_provider,
    usage_text,
)
from .keys import (  # noqa: F401
    cb_key_delete,
    cb_key_delete_ask,
    cb_key_new,
    cb_key_rename,
    cb_key_set,
    cb_keys,
    cb_keys_add,
    cb_keys_screen,
    keys_menu,
    keys_text,
    missing_providers,
    on_key_rename,
    on_key_value,
)
from .providers import (  # noqa: F401
    cb_compare,
    cb_help,
    cb_list,
    cb_live_models,
    cb_menu,
    cb_model,
    cb_priority,
    cb_provider,
    cb_recommend,
    cb_use,
    cb_use_default,
    on_api_key,
    on_model,
)
from .promote import (  # noqa: F401
    cb_forget,
    cb_promote,
    cb_test,
)
