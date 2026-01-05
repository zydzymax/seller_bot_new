"""
Sales Whisper Manager Widget API v2
Backend endpoints for AmoCRM/Kommo marketplace widget

Features:
- Lead/Contact AI assistant
- Message generation
- Objection handling
- Client profiling
- Auto-response management
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Header, Depends, Request
from pydantic import BaseModel

from .amocrm_oauth import get_valid_token, token_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["widget"])


# ============ Authentication ============

async def verify_widget_request(
    request: Request,
    x_account_id: str = Header(None, alias="X-Account-Id"),
    authorization: str = Header(None)
) -> str:
    """
    Verify widget request authentication.
    Widget sends account_id in header, we verify token exists.
    """
    account_id = x_account_id
    
    if not account_id:
        # Try to get from query params (fallback)
        account_id = request.query_params.get('account_id', '')
    
    if not account_id:
        raise HTTPException(status_code=401, detail="Missing account_id")
    
    # Verify account has valid installation
    token = await get_valid_token(account_id)
    if not token:
        raise HTTPException(
            status_code=403, 
            detail="Widget not installed or token expired. Please reinstall."
        )
    
    return account_id


# ============ Pydantic Models ============

class ClientInfo(BaseModel):
    initials: str = "?"
    name: str = ""
    phone: str = ""
    email: str = ""
    company: str = ""


class Message(BaseModel):
    text: str
    type: str = "general"  # general, follow_up, objection, closing
    channel: str = "whatsapp"
    confidence: float = 0.85


class ConversationMessage(BaseModel):
    text: str
    direction: str  # incoming / outgoing
    time: str
    channel: str = "whatsapp"


class PersonalityProfile(BaseModel):
    decision_style: Optional[str] = None  # analytical, intuitive, collaborative
    pace: Optional[str] = None  # fast, moderate, slow
    trust_level: Optional[str] = None  # low, medium, high
    communication: Optional[str] = None  # formal, casual, direct
    key_motivators: List[str] = []


class LeadAssistantResponse(BaseModel):
    lead_id: int
    lead_name: str
    client_info: ClientInfo
    conversation_history: List[ConversationMessage] = []
    suggested_messages: List[Message] = []
    deal_stage: str = "new"
    client_temperature: str = "cold"  # cold, warm, hot
    temperature_score: int = 0  # 0-100
    next_action: str = ""
    talking_points: List[str] = []
    objections: List[str] = []
    buying_signals: List[str] = []
    risks: List[str] = []


class ContactProfileResponse(BaseModel):
    contact_id: int
    contact_name: str
    phone: str = ""
    email: str = ""
    company: str = ""
    position: str = ""
    total_interactions: int = 0
    total_deals: int = 0
    won_deals: int = 0
    personality_profile: PersonalityProfile = PersonalityProfile()
    communication_style: str = "formal"
    best_contact_time: str = ""
    interests: List[str] = []
    past_objections: List[str] = []
    notes: str = ""


class GenerateMessageRequest(BaseModel):
    context: str = "follow_up"  # follow_up, first_contact, objection, closing, reactivation
    tone: str = "professional"  # professional, friendly, urgent
    custom_context: str = ""


class GenerateMessageResponse(BaseModel):
    messages: List[Message]
    context_used: str


class SendMessageRequest(BaseModel):
    message: str
    channel: str = "whatsapp"
    schedule_time: Optional[str] = None


class SendMessageResponse(BaseModel):
    success: bool
    message_id: Optional[str] = None
    scheduled: bool = False
    error: Optional[str] = None


class ObjectionRequest(BaseModel):
    objection_text: str
    deal_context: str = ""


class ObjectionResponse(BaseModel):
    original_objection: str
    objection_type: str  # price, timing, competition, trust, authority, need
    response_options: List[Message]
    tips: List[str]


class AutoResponseSettings(BaseModel):
    enabled: bool
    working_hours_only: bool = True
    working_hours_start: str = "09:00"
    working_hours_end: str = "18:00"
    response_delay_seconds: int = 30
    excluded_stages: List[str] = []


# ============ Mock AI Service ============

class AIAssistantService:
    """
    AI service for generating sales assistance.
    In production, this connects to OpenAI/Claude.
    """
    
    @staticmethod
    async def analyze_lead(lead_data: Dict[str, Any]) -> LeadAssistantResponse:
        """Analyze lead and generate assistant data"""
        # In production: call LLM with lead context
        
        lead_id = lead_data.get('id', 0)
        lead_name = lead_data.get('name', 'Новая сделка')
        
        # Mock response
        return LeadAssistantResponse(
            lead_id=lead_id,
            lead_name=lead_name,
            client_info=ClientInfo(
                initials=lead_name[0] if lead_name else "?",
                name=lead_data.get('contact_name', ''),
                phone=lead_data.get('phone', ''),
                email=lead_data.get('email', ''),
                company=lead_data.get('company', '')
            ),
            conversation_history=[],
            suggested_messages=[
                Message(
                    text="Добрый день! Увидел вашу заявку. Подскажите, какой у вас бюджет на проект?",
                    type="first_contact",
                    confidence=0.9
                ),
                Message(
                    text="Когда вам было бы удобно обсудить детали? Могу позвонить сегодня после 15:00.",
                    type="follow_up",
                    confidence=0.85
                )
            ],
            deal_stage=lead_data.get('stage', 'new'),
            client_temperature="warm",
            temperature_score=65,
            next_action="Уточнить бюджет и сроки",
            talking_points=[
                "Спросить о текущем решении",
                "Выяснить ЛПР",
                "Предложить демо"
            ],
            objections=["Дорого", "Нужно подумать"],
            buying_signals=["Запросил прайс", "Спрашивал о сроках"],
            risks=["Долго не отвечает", "Сравнивает с конкурентами"]
        )
    
    @staticmethod
    async def generate_messages(
        context: str,
        lead_data: Dict[str, Any],
        conversation: List[Dict] = None
    ) -> List[Message]:
        """Generate contextual messages"""
        # In production: call LLM
        
        messages_by_context = {
            "first_contact": [
                Message(text="Добрый день! Меня зовут [Имя], компания Sales Whisper. Увидел вашу заявку на сайте. Удобно сейчас обсудить?", type="first_contact", confidence=0.9),
                Message(text="Здравствуйте! Благодарим за интерес к нашему продукту. Подскажите, какую задачу хотите решить?", type="first_contact", confidence=0.85),
            ],
            "follow_up": [
                Message(text="Добрый день! Возвращаюсь к нашему разговору. Удалось посмотреть материалы, которые отправлял?", type="follow_up", confidence=0.88),
                Message(text="Привет! Как продвигается решение по нашему вопросу? Могу чем-то помочь?", type="follow_up", confidence=0.82),
            ],
            "objection": [
                Message(text="Понимаю ваши сомнения. Давайте разберём подробнее — что именно смущает?", type="objection", confidence=0.87),
            ],
            "closing": [
                Message(text="Отлично! Тогда предлагаю зафиксировать договорённости. Когда удобно подписать договор?", type="closing", confidence=0.9),
                Message(text="Супер! Высылаю счёт на оплату. После поступления средств сразу приступаем.", type="closing", confidence=0.88),
            ],
            "reactivation": [
                Message(text="Давно не общались! Как у вас дела с [проект/задача]? Может, сейчас актуально вернуться к вопросу?", type="reactivation", confidence=0.8),
            ]
        }
        
        return messages_by_context.get(context, messages_by_context["follow_up"])
    
    @staticmethod
    async def handle_objection(objection: str, context: str = "") -> ObjectionResponse:
        """Generate objection handling response"""
        
        # Classify objection
        objection_lower = objection.lower()
        if any(w in objection_lower for w in ['дорого', 'цена', 'бюджет', 'деньги', 'стоимость']):
            obj_type = "price"
            responses = [
                Message(text="Понимаю, бюджет важен. Давайте посмотрим, какой ROI вы получите — обычно наши клиенты окупают вложения за 2-3 месяца.", type="objection"),
                Message(text="Да, инвестиция серьёзная. Но давайте посчитаем, сколько вы сейчас теряете без этого решения?", type="objection"),
            ]
            tips = ["Переведите разговор на ценность", "Покажите ROI на примере", "Предложите рассрочку"]
        
        elif any(w in objection_lower for w in ['подумать', 'время', 'позже', 'потом']):
            obj_type = "timing"
            responses = [
                Message(text="Конечно, это важное решение. Какая информация поможет вам определиться быстрее?", type="objection"),
                Message(text="Понимаю. Давайте я пришлю кейсы похожих компаний — это поможет с решением.", type="objection"),
            ]
            tips = ["Создайте ограниченность", "Выясните истинную причину", "Договоритесь о конкретной дате"]
        
        else:
            obj_type = "general"
            responses = [
                Message(text="Понимаю вашу позицию. Расскажите подробнее, что именно вызывает сомнения?", type="objection"),
            ]
            tips = ["Задайте уточняющий вопрос", "Проявите эмпатию", "Предложите альтернативу"]
        
        return ObjectionResponse(
            original_objection=objection,
            objection_type=obj_type,
            response_options=responses,
            tips=tips
        )


ai_service = AIAssistantService()


# ============ Widget Endpoints ============

@router.get("/lead/{lead_id}/assistant")
async def get_lead_assistant(
    lead_id: int,
    account_id: str = Depends(verify_widget_request)
) -> LeadAssistantResponse:
    """
    Get AI assistant data for a lead.
    Called when opening lead card in AmoCRM.
    """
    # In production: fetch lead data from AmoCRM API
    lead_data = {
        'id': lead_id,
        'name': f'Сделка #{lead_id}',
        'stage': 'negotiation'
    }
    
    return await ai_service.analyze_lead(lead_data)


@router.post("/lead/{lead_id}/generate")
async def generate_lead_messages(
    lead_id: int,
    request: GenerateMessageRequest,
    account_id: str = Depends(verify_widget_request)
) -> GenerateMessageResponse:
    """Generate contextual messages for lead"""
    messages = await ai_service.generate_messages(
        context=request.context,
        lead_data={'id': lead_id}
    )
    
    return GenerateMessageResponse(
        messages=messages,
        context_used=request.context
    )


@router.post("/lead/{lead_id}/send")
async def send_message_to_lead(
    lead_id: int,
    request: SendMessageRequest,
    account_id: str = Depends(verify_widget_request)
) -> SendMessageResponse:
    """Send message to lead via specified channel"""
    # In production: integrate with WhatsApp/Telegram APIs
    
    logger.info(f"Sending message to lead {lead_id} via {request.channel}")
    
    return SendMessageResponse(
        success=True,
        message_id=f"msg_{lead_id}_{datetime.utcnow().timestamp()}",
        scheduled=request.schedule_time is not None
    )


@router.get("/contact/{contact_id}/profile")
async def get_contact_profile(
    contact_id: int,
    account_id: str = Depends(verify_widget_request)
) -> ContactProfileResponse:
    """Get AI-analyzed contact profile"""
    # In production: fetch from AmoCRM and analyze
    
    return ContactProfileResponse(
        contact_id=contact_id,
        contact_name=f"Контакт #{contact_id}",
        total_interactions=15,
        total_deals=3,
        won_deals=2,
        personality_profile=PersonalityProfile(
            decision_style="analytical",
            pace="moderate",
            trust_level="medium",
            communication="formal",
            key_motivators=["ROI", "Надёжность", "Поддержка"]
        ),
        communication_style="formal",
        best_contact_time="Вторник-четверг, 14:00-17:00",
        interests=["Автоматизация", "Аналитика"],
        past_objections=["Цена", "Сроки внедрения"]
    )


@router.post("/objection/handle")
async def handle_objection(
    request: ObjectionRequest,
    account_id: str = Depends(verify_widget_request)
) -> ObjectionResponse:
    """Get AI response to client objection"""
    return await ai_service.handle_objection(
        objection=request.objection_text,
        context=request.deal_context
    )


@router.get("/settings/{account_id}")
async def get_widget_settings(
    account_id: str
) -> AutoResponseSettings:
    """Get widget settings for account"""
    # In production: load from database
    return AutoResponseSettings(
        enabled=False,
        working_hours_only=True,
        working_hours_start="09:00",
        working_hours_end="18:00",
        response_delay_seconds=30
    )


@router.post("/settings/{account_id}")
async def save_widget_settings(
    account_id: str,
    settings: AutoResponseSettings
) -> Dict[str, Any]:
    """Save widget settings for account"""
    # In production: save to database
    logger.info(f"Settings saved for account {account_id}: {settings.dict()}")
    return {"success": True, "message": "Settings saved"}


@router.get("/health")
async def widget_health():
    """Widget API health check"""
    return {
        "status": "healthy",
        "service": "saleswhisper-widget",
        "version": "2.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


# ============================================
# HEAD OF SALES MODULE - Analytics API
# ============================================

class HOSDashboardData(BaseModel):
    """Head of Sales dashboard data"""
    total_deals: int = 0
    total_revenue: float = 0
    conversion_rate: float = 0
    avg_deal_time: str = "-"
    deals_change: float = 0
    revenue_change: float = 0
    conversion_change: float = 0
    cycle_change: float = 0
    managers: List[Dict[str, Any]] = []
    funnel_stages: List[Dict[str, Any]] = []
    insights: List[Dict[str, Any]] = []
    forecast_revenue: float = 0
    forecast_deals: int = 0
    forecast_confidence: int = 0


class ManagerPerformance(BaseModel):
    """Manager performance data"""
    id: int
    name: str
    avatar: str = ""
    deals_count: int = 0
    revenue: float = 0
    conversion: float = 0
    rating: str = "A"
    rating_class: str = "good"


@router.get("/hos/dashboard")
async def get_hos_dashboard(
    period: int = 30,
    account_id: str = Depends(verify_widget_request)
) -> HOSDashboardData:
    """
    Get Head of Sales dashboard data
    
    Args:
        period: Number of days for analytics
        account_id: AmoCRM account ID
    """
    logger.info(f"HOS Dashboard request: account={account_id}, period={period}")
    
    # In production: fetch real data from AmoCRM API and analytics service
    # This is mock data for demonstration
    
    return HOSDashboardData(
        total_deals=47,
        total_revenue=2850000,
        conversion_rate=23.5,
        avg_deal_time="12 дней",
        deals_change=15.3,
        revenue_change=22.1,
        conversion_change=3.2,
        cycle_change=-8.5,
        managers=[
            {
                "id": 1,
                "name": "Алексей Петров",
                "avatar": "",
                "deals_count": 15,
                "revenue": "950 000 ₽",
                "conversion": 28,
                "rating": "A+",
                "rating_class": "excellent"
            },
            {
                "id": 2,
                "name": "Мария Иванова",
                "avatar": "",
                "deals_count": 12,
                "revenue": "780 000 ₽",
                "conversion": 25,
                "rating": "A",
                "rating_class": "good"
            },
            {
                "id": 3,
                "name": "Дмитрий Сидоров",
                "avatar": "",
                "deals_count": 10,
                "revenue": "620 000 ₽",
                "conversion": 20,
                "rating": "B+",
                "rating_class": "average"
            }
        ],
        funnel_stages=[
            {"name": "Новые", "count": 156, "width": 100, "color": "#4A90D9", "conversion": 100},
            {"name": "Квалификация", "count": 98, "width": 63, "color": "#7B68EE", "conversion": 63},
            {"name": "Презентация", "count": 67, "width": 43, "color": "#9370DB", "conversion": 68},
            {"name": "КП отправлено", "count": 45, "width": 29, "color": "#BA55D3", "conversion": 67},
            {"name": "Переговоры", "count": 32, "width": 21, "color": "#DA70D6", "conversion": 71},
            {"name": "Закрыто", "count": 24, "width": 15, "color": "#32CD32", "conversion": 75}
        ],
        insights=[
            {
                "type": "warning",
                "icon": "⚠️",
                "text": "15 сделок на этапе 'КП отправлено' более 7 дней без движения"
            },
            {
                "type": "success",
                "icon": "📈",
                "text": "Конверсия выросла на 3.2% по сравнению с прошлым периодом"
            },
            {
                "type": "info",
                "icon": "💡",
                "text": "Лучшее время для звонков: 10:00-12:00 (конверсия +18%)"
            },
            {
                "type": "alert",
                "icon": "🔔",
                "text": "У менеджера Сидорова 5 просроченных задач"
            }
        ],
        forecast_revenue=3200000,
        forecast_deals=52,
        forecast_confidence=78
    )


@router.get("/hos/managers")
async def get_managers_performance(
    period: int = 30,
    account_id: str = Depends(verify_widget_request)
) -> List[ManagerPerformance]:
    """Get detailed managers performance"""
    
    # In production: fetch from analytics service
    return [
        ManagerPerformance(
            id=1,
            name="Алексей Петров",
            deals_count=15,
            revenue=950000,
            conversion=28,
            rating="A+",
            rating_class="excellent"
        )
    ]


@router.get("/hos/funnel")
async def get_funnel_analytics(
    period: int = 30,
    pipeline_id: Optional[int] = None,
    account_id: str = Depends(verify_widget_request)
) -> Dict[str, Any]:
    """Get detailed funnel analytics"""
    
    return {
        "stages": [],
        "conversion_rate": 23.5,
        "avg_time_in_funnel": "14 дней",
        "bottlenecks": []
    }


@router.get("/hos/export")
async def export_analytics(
    period: int = 30,
    format: str = "xlsx",
    account_id: str = Depends(verify_widget_request)
):
    """Export analytics report"""
    from fastapi.responses import StreamingResponse
    import io
    
    # In production: generate real report
    # For now, return a placeholder
    content = f"Analytics Report - Period: {period} days\nAccount: {account_id}\n"
    
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=analytics_{period}d.txt"
        }
    )


@router.get("/hos/forecast")
async def get_sales_forecast(
    period: int = 30,
    account_id: str = Depends(verify_widget_request)
) -> Dict[str, Any]:
    """Get AI-powered sales forecast"""
    
    return {
        "forecast_revenue": 3200000,
        "forecast_deals": 52,
        "confidence": 78,
        "factors": [
            {"factor": "Сезонность", "impact": "+12%"},
            {"factor": "Тренд конверсии", "impact": "+5%"},
            {"factor": "Активность лидов", "impact": "-3%"}
        ],
        "recommendations": [
            "Увеличить активность на этапе 'Квалификация'",
            "Провести обучение по работе с возражениями",
            "Оптимизировать время отклика на новые заявки"
        ]
    }
