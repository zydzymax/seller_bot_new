"""
flow_manager.py — Профессиональный менеджер диалогов для AI-продавца SoVAni (GPT-4o + аналитика)
© SoVAni 2025
"""

import os
import datetime
from typing import Dict, List, Any
import structlog
from adapters.openai_adapter import OpenAIAdapter
from utils.prompt_manager import build_prompt
from utils.sales_analyzer import get_current_stage, get_current_emotion, get_client_info

logger = structlog.get_logger("ai_seller.flow_manager")

class FlowManager:
    def __init__(self, redis_url=None):
        self.openai = OpenAIAdapter()
        self.redis_url = redis_url  # future: хранить контекст в redis
        
        # Рабочие часы MSK (8:00-20:00)
        self.work_hours = (8, 20)
        
        # 8-этапная воронка выявления потребностей SoVAni
        self.needs_assessment_steps = [
            "product_type",     # 1. Тип изделия (одежда)
            "work_format",      # 2. Схема работы (давальческая/полный цикл)
            "quantity",         # 3. Объём партии (по модели и цвету)
            "timeline",         # 4. Сроки пошива (когда нужен тираж)
            "budget",           # 5. Ориентир бюджета
            "patterns",         # 6. Лекала (есть/строить)
            "references",       # 7. Фото/референсы
            "contacts"          # 8. Контакты (имя и телефон)
        ]

    def should_greet(self, user_id: int, context: Dict) -> bool:
        """Определяет нужно ли приветствие (один раз за рабочий день)"""
        now = datetime.datetime.now()
        moscow_hour = (now.hour + 3) % 24  # Простое приведение к MSK
        
        # Не рабочие часы - не приветствуем активно
        if not (self.work_hours[0] <= moscow_hour < self.work_hours[1]):
            return False
            
        last_greeting = context.user_data.get("last_greeting_date")
        today = now.date().isoformat()
        
        return last_greeting != today
        
    def get_needs_assessment_progress(self, context: Dict) -> Dict[str, Any]:
        """Получает прогресс выявления потребностей"""
        progress = context.user_data.get("needs_assessment", {})
        return {
            "current_step": progress.get("current_step", 0),
            "completed_steps": progress.get("completed_steps", {}),
            "is_qualified": progress.get("is_qualified", False),
            "disqualified_reason": progress.get("disqualified_reason", None)
        }
        
    def update_needs_assessment(self, context: Dict, step_data: Dict) -> None:
        """Обновляет прогресс выявления потребностей"""
        if "needs_assessment" not in context.user_data:
            context.user_data["needs_assessment"] = {
                "current_step": 0,
                "completed_steps": {},
                "is_qualified": False
            }
            
        progress = context.user_data["needs_assessment"]
        current_step = progress["current_step"]
        
        if current_step < len(self.needs_assessment_steps):
            step_key = self.needs_assessment_steps[current_step]
            progress["completed_steps"][step_key] = step_data
            progress["current_step"] = current_step + 1
            
            # Проверка завершения всех этапов
            if progress["current_step"] >= len(self.needs_assessment_steps):
                progress["is_qualified"] = True
                self.log_qualified_lead(context.user_data)
                
    def log_qualified_lead(self, user_data: Dict) -> None:
        """Логирует квалифицированного лида для передачи ответственным"""
        assessment = user_data.get("needs_assessment", {})
        completed = assessment.get("completed_steps", {})
        
        lead_info = {
            "timestamp": datetime.datetime.now().isoformat(),
            "product_type": completed.get("product_type", "Не указано"),
            "work_format": completed.get("work_format", "Не указано"),
            "quantity": completed.get("quantity", "Не указано"),
            "timeline": completed.get("timeline", "Не указано"),
            "patterns": completed.get("patterns", "Не указано"),
            "reference_photo": completed.get("reference_photo", "Не предоставлено"),
            "user_history": user_data.get("history", [])
        }
        
        logger.info("QUALIFIED_LEAD_READY", lead_data=lead_info)
        print(f"\n=== КВАЛИФИЦИРОВАННЫЙ ЛИД ===\n{lead_info}\n===========================\n")
    
    async def process(self, user_id, message, context):
        """
        Двойная обработка: ChatGPT (логика + возражения) -> Claude (эмоциональность)
        Алена - продавец SoVAni, 6-этапное выявление потребностей
        """
        logger.info("user_message_received", user_id=user_id, length=len(message))
        
        # История пользователя
        history = context.user_data.get("history", [])
        history.append({"role": "user", "content": message})
        
        # Проверка приветствия
        should_greet = self.should_greet(user_id, context)
        if should_greet:
            context.user_data["last_greeting_date"] = datetime.datetime.now().date().isoformat()
            
        # Прогресс выявления потребностей
        needs_progress = self.get_needs_assessment_progress(context)
        
        # Анализ этапа продаж и эмоций  
        stage = "cold" if should_greet else get_current_stage(history)
        emotion = get_current_emotion(history)
        client_info = get_client_info(history)
        
        logger.info("sales_analysis", user_id=user_id, stage=stage, emotion=emotion,
                   needs_step=needs_progress["current_step"], qualified=needs_progress["is_qualified"])
        
        # Формируем контекст для ChatGPT
        gpt_context = self._build_gpt_context(stage, emotion, history, needs_progress, 
                                              should_greet, client_info)
        
        # ЭТАП 1: ChatGPT - логика, скрипт, возражения
        try:
            gpt_response = await self.openai.generate(
                messages=gpt_context,
                temperature=0.6,  # Баланс логики и креативности
                max_tokens=800
            )
            logger.info("ChatGPT response OK", length=len(gpt_response))
            
            # Используем прямой ответ от GPT-5
            final_response = gpt_response
            logger.info("GPT-5 response ready", length=len(final_response))
            
            # Добавляем финальный ответ в историю
            history.append({"role": "assistant", "content": final_response})
            
            # Автоматически обновляем прогресс needs assessment
            self._auto_update_needs_progress(message, final_response, context)
            
            # Сохраняем контекст
            context.user_data["history"] = history[-12:]
            context.user_data["current_stage"] = stage
            context.user_data["client_info"] = client_info
            
            return final_response
            
        except Exception as e:
            logger.error("dual_llm_processing_error", error=str(e))
            return "⚠️ Извините, произошла техническая ошибка. Попробуйте позже."
            
    def _build_gpt_context(self, stage: str, emotion: str, history: List, 
                           needs_progress: Dict, should_greet: bool, client_info: Dict) -> List[Dict]:
        """Строит контекст для ChatGPT с фокусом на логику и скрипт"""
        # Базовый промпт для ChatGPT
        gpt_prompt = build_prompt(llm="gpt-4", stage=stage, emotion=emotion, history=history[-8:])
        
        # Добавляем информацию о прогрессе выявления потребностей
        if needs_progress["current_step"] < len(self.needs_assessment_steps):
            current_step_name = self.needs_assessment_steps[needs_progress["current_step"]]
            gpt_prompt += f"\n\nТЕКУЩИЙ ЭТАП: {current_step_name} ({needs_progress['current_step']+1}/6)"
            gpt_prompt += "\nЗАДАЧА: Задай ОДИН конкретный вопрос для этого этапа. НЕ переходи к следующему!"
            
        # Добавляем контекст приветствия
        if should_greet:
            gpt_prompt += "\n\nОБЯЗАТЕЛЬНО: Начни с приветствия Алены от SoVAni и сразу спроси о проекте."
            
        # Добавляем информацию о клиенте
        if client_info.get('mentioned_products'):
            gpt_prompt += f"\n\nКЛИЕНТ УЖЕ СООБЩИЛ: продукты - {client_info['mentioned_products']}"
        if client_info.get('mentioned_quantities'):
            gpt_prompt += f"\nКЛИЕНТ УЖЕ СООБЩИЛ: количество - {client_info['mentioned_quantities']}"
            
        # Завершённые этапы
        completed = needs_progress.get("completed_steps", {})
        if completed:
            gpt_prompt += "\n\nЗАВЕРШЁННЫЕ ЭТАПЫ:"
            for step_key, step_data in completed.items():
                gpt_prompt += f"\n- {step_key}: {step_data}"
                
        return [{"role": "system", "content": gpt_prompt}] + history[-6:]
        
        
    def _auto_update_needs_progress(self, user_message: str, bot_response: str, context: Dict) -> None:
        """Автоматически обновляет прогресс needs assessment на основе диалога"""
        needs_progress = self.get_needs_assessment_progress(context)
        current_step = needs_progress["current_step"]
        
        if current_step >= len(self.needs_assessment_steps):
            return  # Все этапы уже завершены
            
        step_key = self.needs_assessment_steps[current_step]
        
        # Определяем, что пользователь ответил на текущий этап
        user_msg_lower = user_message.lower()
        
        step_data = None
        if step_key == "product_type":
            if any(word in user_msg_lower for word in ["пижам", "худи", "футболк", "костюм", "свитшот"]):
                step_data = user_message
        elif step_key == "work_format":
            if any(word in user_msg_lower for word in ["ключ", "давальческ", "сырье", "ткань"]):
                step_data = user_message
        elif step_key == "quantity":
            if any(word in user_msg_lower for word in ["штук", "единиц", "1000", "500", "300"]):
                step_data = user_message
        elif step_key == "timeline":
            if any(word in user_msg_lower for word in ["дн", "недел", "месяц", "срочно", "быстро", "когда", "срок"]):
                step_data = user_message
        elif step_key == "budget":
            if any(word in user_msg_lower for word in ["рублей", "тысяч", "бюджет", "ориентир", "примерно", "около"]):
                step_data = user_message
        elif step_key == "patterns":
            if any(word in user_msg_lower for word in ["есть", "готов", "нет", "строить", "лекала"]):
                step_data = user_message
        elif step_key == "references":
            # Фото обрабатывается отдельно в process_photo
            pass
        elif step_key == "contacts":
            if any(word in user_msg_lower for word in ["телефон", "номер", "связи", "+7", "8-", "89"]):
                step_data = user_message
            
        if step_data:
            self.update_needs_assessment(context, step_data)

    async def process_photo(self, user_id, photos, context):
        """Обработка фото - 7-й этап воронки (references)"""
        logger.info("photo_received", user_id=user_id, photo_count=len(photos))
        
        # Отмечаем получение фото (7-й этап)
        self.update_needs_assessment(context, f"Получено {len(photos)} фото")
        
        # Проверяем, завершили ли мы все этапы
        needs_progress = self.get_needs_assessment_progress(context)
        
        if needs_progress["current_step"] >= len(self.needs_assessment_steps):
            # Все 8 этапов пройдены - передаем в CRM
            return ("Отлично! Все данные собраны. Передам менеджеру для расчёта, подскажите номер для связи.")
        else:
            # Остался только этап контактов
            return "Фото принято! Передам менеджеру для расчёта, подскажите номер для связи."

    async def process_voice(self, user_id, voice, context):
        """Заглушка для голосовых сообщений"""
        return "⚠️ Голосовые сообщения пока не поддерживаются. Напишите текстом."

    async def process_audio(self, user_id, audio, context):
        """Заглушка для аудио файлов"""
        return "⚠️ Аудио файлы пока не поддерживаются. Напишите текстом."
