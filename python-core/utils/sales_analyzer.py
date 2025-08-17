"""
sales_analyzer.py — Анализатор этапов продаж и эмоционального состояния клиента
© SoVAni 2025
"""

import re
from typing import List, Dict, Tuple


class SalesStageAnalyzer:
    """Определяет текущий этап продаж на основе анализа диалога"""
    
    def __init__(self):
        # Ключевые слова для определения этапов
        self.stage_keywords = {
            'cold': [
                'привет', 'здравствуйте', 'добрый день', 'хочу узнать', 'интересует',
                'можете рассказать', 'что предлагаете', 'услышал о вас'
            ],
            'interested': [
                'сколько стоит', 'какие цены', 'что по срокам', 'объемы', 'тираж',
                'возможно ли', 'а если', 'нужна ли', 'можно ли', 'какие варианты'
            ],
            'qualifying': [
                'опыт работы', 'портфолио', 'примеры', 'клиенты', 'качество',
                'гарантии', 'как работаете', 'процесс', 'этапы', 'контроль'
            ],
            'negotiating': [
                'цена', 'стоимость', 'бюджет', 'оплата', 'скидка', 'условия',
                'договор', 'аванс', 'рассрочка', 'выгодно ли', 'дорого'
            ],
            'closing': [
                'когда начнем', 'как оформить', 'договор', 'заказ', 'согласен',
                'подходит', 'устраивает', 'давайте', 'хорошо', 'решение'
            ],
            'objection': [
                'но', 'однако', 'проблема в том', 'не устраивает', 'сомневаюсь',
                'дорого', 'долго', 'подумаю', 'не уверен', 'есть вопросы'
            ]
        }
        
        # Индикаторы эмоционального состояния
        self.emotion_indicators = {
            'positive': [
                'отлично', 'супер', 'замечательно', 'хорошо', 'интересно',
                'нравится', 'подходит', 'именно то что нужно'
            ],
            'frustrated': [
                'не понимаю', 'сложно', 'проблема', 'не получается', 'запутался',
                'везде одно и то же', 'никто не может', 'уже устал'
            ],
            'skeptical': [
                'сомневаюсь', 'не уверен', 'возможно', 'может быть', 'проверить',
                'а вдруг', 'не обманете', 'правда ли', 'точно ли'
            ],
            'excited': [
                'отлично!', 'да!', 'именно!', 'точно!', 'давайте!', 'срочно',
                'как можно быстрее', 'прям то что искал'
            ]
        }

    def analyze_stage(self, history: List[Dict]) -> str:
        """Определяет текущий этап продаж на основе истории диалога"""
        if not history:
            return 'cold'
        
        # Берем последние 4 сообщения для анализа
        recent_messages = history[-4:]
        user_messages = [msg['content'].lower() for msg in recent_messages if msg['role'] == 'user']
        
        if not user_messages:
            return 'cold'
        
        # Подсчитываем совпадения по этапам
        stage_scores = {}
        for stage, keywords in self.stage_keywords.items():
            score = 0
            for message in user_messages:
                for keyword in keywords:
                    if keyword in message:
                        score += 1
            stage_scores[stage] = score
        
        # Логика определения этапа
        latest_message = user_messages[-1]
        
        # Если есть возражения - сразу objection
        if stage_scores.get('objection', 0) > 0 and any(word in latest_message for word in ['не', 'но', 'однако']):
            return 'objection'
        
        # Если готовы к закрытию
        if stage_scores.get('closing', 0) > 0:
            return 'closing'
        
        # Если обсуждают цены и условия
        if stage_scores.get('negotiating', 0) > 0:
            return 'negotiating'
        
        # Если интересуются качеством и опытом
        if stage_scores.get('qualifying', 0) > 0:
            return 'qualifying'
        
        # Если задают вопросы о продукте
        if stage_scores.get('interested', 0) > 0 or '?' in latest_message:
            return 'interested'
        
        # По умолчанию - первичный контакт
        return 'cold'

    def analyze_emotion(self, history: List[Dict]) -> str:
        """Определяет эмоциональное состояние клиента"""
        if not history:
            return 'neutral'
        
        # Анализируем последние 2 сообщения пользователя
        user_messages = [msg['content'].lower() for msg in history[-4:] if msg['role'] == 'user']
        
        if not user_messages:
            return 'neutral'
        
        latest_message = user_messages[-1]
        
        # Подсчитываем эмоциональные индикаторы
        emotion_scores = {}
        for emotion, indicators in self.emotion_indicators.items():
            score = 0
            for message in user_messages:
                for indicator in indicators:
                    if indicator in message:
                        score += 1
            emotion_scores[emotion] = score
        
        # Определяем доминирующую эмоцию
        if emotion_scores.get('excited', 0) > 0 and ('!' in latest_message or 'срочно' in latest_message):
            return 'excited'
        
        if emotion_scores.get('frustrated', 0) > 0:
            return 'frustrated'
        
        if emotion_scores.get('skeptical', 0) > 0:
            return 'skeptical'
        
        if emotion_scores.get('positive', 0) > 0:
            return 'positive'
        
        return 'neutral'

    def extract_client_info(self, history: List[Dict]) -> Dict:
        """Извлекает ключевую информацию о клиенте из диалога"""
        info = {
            'mentioned_products': [],
            'mentioned_quantities': [],
            'mentioned_timeframes': [],
            'budget_signals': [],
            'requirements': []
        }
        
        user_messages = [msg['content'] for msg in history if msg['role'] == 'user']
        text = ' '.join(user_messages).lower()
        
        # Продукты
        products = ['футболка', 'футболки', 'худи', 'толстовка', 'свитшот', 'поло', 'майка']
        for product in products:
            if product in text:
                info['mentioned_products'].append(product)
        
        # Количества
        quantities = re.findall(r'\d+\s*(?:шт|штук|единиц|тысяч)', text)
        info['mentioned_quantities'] = quantities
        
        # Сроки
        timeframes = re.findall(r'(?:через|до|к)\s+\d+\s*(?:дня|дней|недель|месяца|месяцев)', text)
        info['mentioned_timeframes'] = timeframes
        
        # Бюджет
        budget_words = ['бюджет', 'стоимость', 'цена', 'рублей', 'тысяч', 'млн']
        for word in budget_words:
            if word in text:
                info['budget_signals'].append(word)
        
        return info


# Глобальный экземпляр анализатора
sales_analyzer = SalesStageAnalyzer()


def get_current_stage(history: List[Dict]) -> str:
    """Определяет текущий этап продаж"""
    return sales_analyzer.analyze_stage(history)


def get_current_emotion(history: List[Dict]) -> str:
    """Определяет эмоциональное состояние клиента"""
    return sales_analyzer.analyze_emotion(history)


def get_client_info(history: List[Dict]) -> Dict:
    """Извлекает информацию о клиенте"""
    return sales_analyzer.extract_client_info(history)