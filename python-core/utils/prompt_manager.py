"""
prompt_manager.py — генератор структурированных промптов для AI-продавца SoVAni
© SoVAni 2025
"""

import yaml
import os

PROMPTS_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'prompts.yaml')

def load_prompts():
    with open(PROMPTS_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

PROMPTS = load_prompts()

def build_prompt(llm: str, stage: str, emotion: str, history=None) -> str:
    """
    Формирует структурированный промпт для выбранного LLM, этапа и эмоции.
    llm: 'gpt-4' или 'claude-3'
    stage: стадия (cold/interested/qualifying/negotiating/closing/objection)
    emotion: эмоциональное состояние (neutral/positive/frustrated/skeptical/excited)
    history: список сообщений (для краткой истории)
    """
    blocks = []
    # 1. Базовая роль
    blocks.append(PROMPTS['base_roles'].get(llm, ""))
    # 2. Этап воронки
    stage_block = PROMPTS.get('stage_instructions', {}).get(stage, "")
    if stage_block:
        blocks.append(f"# Этап: {stage.capitalize()}\n{stage_block}")
    # 3. Эмоция
    emotion_block = PROMPTS.get('emotional_adaptations', {}).get(emotion, "")
    if emotion_block:
        blocks.append(f"# Эмоция: {emotion_block.strip()}")
    # 4. Ограничения/правила
    blocks.append(PROMPTS.get('constraints', ""))
    # 5. (Опционально) История
    if history and len(history) > 1:
        summary = make_short_history(history)
        blocks.append(f"# История:\n{summary}")
    return "\n\n".join([b.strip() for b in blocks if b.strip()])

def make_short_history(history):
    # Вытаскиваем последние 2-3 реплики для LLM-контекста
    msgs = history[-6:] if len(history) > 6 else history
    out = []
    for msg in msgs:
        role = msg.get("role", "user")
        prefix = "Клиент:" if role == "user" else "Менеджер:"
        out.append(f"{prefix} {msg['content']}")
    return "\n".join(out)
