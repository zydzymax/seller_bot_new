"""
Sales Whisper Manager Widget API
Backend endpoints for AmoCRM/Kommo widget integration
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["widget"])


# ============ Pydantic Models ============

class ClientInfo(BaseModel):
    initials: str = "?"
    name: str = ""
    phone: str = ""


class Message(BaseModel):
    text: str
    type: str = "general"
    channel: str = "whatsapp"


class ConversationMessage(BaseModel):
    text: str
    direction: str  # "incoming" or "outgoing"
    time: str


class PersonalityProfile(BaseModel):
    decision_style: Optional[str] = None
    pace: Optional[str] = None
    trust_level: Optional[str] = None
    communication: Optional[str] = None


class LeadAssistantData(BaseModel):
    lead_name: str
    client_info: ClientInfo
    conversation_history: List[ConversationMessage] = []
    suggested_messages: List[Message] = []
    deal_stage: str = "new"
    client_temperature: str = "cold"
    next_action: str = ""
    talking_points: List[str] = []
    objections: List[str] = []


class ContactProfileData(BaseModel):
    contact_name: str
    phone: str = ""
    total_interactions: int = 0
    personality_profile: PersonalityProfile = PersonalityProfile()
    communication_style: str = "formal"
    best_contact_time: str = ""
    interests: List[str] = []
    past_objections: List[str] = []


class SendMessageRequest(BaseModel):
    message: str
    channel: str = "whatsapp"


class GenerateMessageRequest(BaseModel):
    context: str = "follow_up"


class AutoResponseRequest(BaseModel):
    enabled: bool


class ObjectionResponse(BaseModel):
    text: str
    effectiveness: str = "standard"


class ObjectionData(BaseModel):
    responses: List[ObjectionResponse] = []
    examples: List[Dict[str, str]] = []
    tips: List[str] = []


# ============ API Key Verification ============

async def verify_api_key(x_api_key: str = Header(None)):
    """Verify API key from widget"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    # TODO: Implement actual API key verification against database
    # For now, accept any non-empty key
    return x_api_key


# ============ Widget Endpoints ============

@router.get("/health")
async def health_check():
    """Health check endpoint for widget connection test"""
    return {
        "status": "ok",
        "service": "Sales Whisper Manager",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/lead/{lead_id}/assistant")
async def get_lead_assistant(
    lead_id: int,
    api_key: str = Depends(verify_api_key)
) -> LeadAssistantData:
    """Get AI assistant data for a lead"""
    try:
        # TODO: Integrate with actual lead storage and AI services
        # For now, return mock data for demonstration

        # Import lead storage if available
        try:
            from services.leads_storage import LeadsStorage
            storage = LeadsStorage()
            lead_data = await storage.get_lead(lead_id)

            if lead_data:
                return LeadAssistantData(
                    lead_name=lead_data.get("name", f"Lead #{lead_id}"),
                    client_info=ClientInfo(
                        initials=lead_data.get("name", "?")[0].upper() if lead_data.get("name") else "?",
                        name=lead_data.get("name", ""),
                        phone=lead_data.get("phone", "")
                    ),
                    conversation_history=_format_conversation(lead_data.get("messages", [])),
                    suggested_messages=await _generate_suggestions(lead_data),
                    deal_stage=lead_data.get("stage", "new"),
                    client_temperature=_calculate_temperature(lead_data),
                    next_action=await _get_next_action(lead_data),
                    talking_points=await _get_talking_points(lead_data),
                    objections=lead_data.get("objections", [])
                )
        except ImportError:
            logger.warning("LeadsStorage not available, using mock data")

        # Return mock data if no real data available
        return LeadAssistantData(
            lead_name=f"Lead #{lead_id}",
            client_info=ClientInfo(initials="L"),
            conversation_history=[],
            suggested_messages=[
                Message(
                    text="Здравствуйте! Хотел уточнить, успели ли вы ознакомиться с нашим предложением?",
                    type="follow_up",
                    channel="whatsapp"
                ),
                Message(
                    text="Добрый день! Напоминаю о нашей договоренности. Когда вам будет удобно обсудить детали?",
                    type="follow_up",
                    channel="whatsapp"
                )
            ],
            deal_stage="negotiation",
            client_temperature="warm",
            next_action="Связаться для уточнения деталей",
            talking_points=[
                "Упомянуть акцию до конца месяца",
                "Уточнить бюджет и сроки",
                "Предложить демо-версию"
            ],
            objections=["Дорого", "Нужно подумать"]
        )

    except Exception as e:
        logger.error(f"Error getting lead assistant data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/contact/{contact_id}/profile")
async def get_contact_profile(
    contact_id: int,
    api_key: str = Depends(verify_api_key)
) -> ContactProfileData:
    """Get contact profile with AI insights"""
    try:
        # TODO: Integrate with actual contact storage
        # Return mock data for demonstration
        return ContactProfileData(
            contact_name=f"Contact #{contact_id}",
            phone="+7 (999) 123-45-67",
            total_interactions=15,
            personality_profile=PersonalityProfile(
                decision_style="Аналитический",
                pace="Размеренный",
                trust_level="Средний",
                communication="Факты и цифры"
            ),
            communication_style="formal",
            best_contact_time="10:00-12:00, будние дни",
            interests=["Качество", "Гарантии", "Сервис"],
            past_objections=["Высокая цена", "Долгие сроки"]
        )

    except Exception as e:
        logger.error(f"Error getting contact profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lead/{lead_id}/send-message")
async def send_message(
    lead_id: int,
    request: SendMessageRequest,
    api_key: str = Depends(verify_api_key)
):
    """Send message to lead via specified channel"""
    try:
        logger.info(f"Sending message to lead {lead_id} via {request.channel}: {request.message[:50]}...")

        # TODO: Integrate with actual messaging services (WhatsApp, SMS)
        # For now, log and return success

        return {
            "status": "sent",
            "lead_id": lead_id,
            "channel": request.channel,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lead/{lead_id}/generate-message")
async def generate_message(
    lead_id: int,
    request: GenerateMessageRequest,
    api_key: str = Depends(verify_api_key)
):
    """Generate AI message for lead based on context"""
    try:
        # TODO: Integrate with LLM service for message generation

        context_messages = {
            "follow_up": "Здравствуйте! Хотел вернуться к нашему разговору. Скажите, появились ли у вас вопросы по предложению?",
            "objection": "Понимаю ваши сомнения. Давайте я расскажу подробнее о наших преимуществах и специальных условиях.",
            "close": "Отлично! Если все устраивает, предлагаю зафиксировать договоренности. Когда вам удобно подписать документы?"
        }

        message = context_messages.get(
            request.context,
            "Добрый день! Напоминаю о нашем предложении. Буду рад ответить на любые вопросы."
        )

        return {
            "message": message,
            "context": request.context
        }

    except Exception as e:
        logger.error(f"Error generating message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lead/{lead_id}/auto-response")
async def toggle_auto_response(
    lead_id: int,
    request: AutoResponseRequest,
    api_key: str = Depends(verify_api_key)
):
    """Toggle auto-response for lead"""
    try:
        logger.info(f"Auto-response for lead {lead_id}: {request.enabled}")

        # TODO: Save to database

        return {
            "lead_id": lead_id,
            "auto_response_enabled": request.enabled
        }

    except Exception as e:
        logger.error(f"Error toggling auto-response: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/objection/{objection}")
async def get_objection_handling(
    objection: str,
    api_key: str = Depends(verify_api_key)
) -> ObjectionData:
    """Get AI-powered objection handling suggestions"""
    try:
        # TODO: Integrate with LLM for dynamic objection handling

        objection_responses = {
            "Дорого": ObjectionData(
                responses=[
                    ObjectionResponse(
                        text="Понимаю, цена важна. Давайте посмотрим, какую ценность вы получаете за эти деньги. Наш продукт окупается за 3 месяца благодаря...",
                        effectiveness="high"
                    ),
                    ObjectionResponse(
                        text="Согласен, это инвестиция. Но давайте посчитаем: сколько вы сейчас тратите на... и сколько сэкономите с нами.",
                        effectiveness="high"
                    ),
                    ObjectionResponse(
                        text="Я могу предложить рассрочку или специальные условия. Какой вариант был бы удобнее?",
                        effectiveness="medium"
                    )
                ],
                examples=[
                    {
                        "objection": "Это слишком дорого для нас",
                        "response": "Понимаю. А если я покажу, как это окупится за 2 месяца?",
                        "result": "Клиент согласился на демо"
                    }
                ],
                tips=[
                    "Никогда не оправдывайтесь за цену",
                    "Переведите разговор на ценность и ROI",
                    "Предложите гибкие условия оплаты"
                ]
            ),
            "Нужно подумать": ObjectionData(
                responses=[
                    ObjectionResponse(
                        text="Конечно, это важное решение. Скажите, что именно хотели бы обдумать? Возможно, я могу помочь с информацией.",
                        effectiveness="high"
                    ),
                    ObjectionResponse(
                        text="Понимаю. Давайте я подготовлю краткое резюме нашего разговора, чтобы вам было удобнее принять решение.",
                        effectiveness="medium"
                    )
                ],
                examples=[
                    {
                        "objection": "Мне нужно посоветоваться с партнером",
                        "response": "Отлично! Может, организуем звонок втроем?",
                        "result": "Назначена встреча с ЛПР"
                    }
                ],
                tips=[
                    "Выясните истинную причину возражения",
                    "Предложите помощь в принятии решения",
                    "Договоритесь о конкретной дате следующего контакта"
                ]
            )
        }

        # Return specific or default objection handling
        return objection_responses.get(objection, ObjectionData(
            responses=[
                ObjectionResponse(
                    text="Я вас понимаю. Расскажите подробнее, что вас беспокоит?",
                    effectiveness="medium"
                )
            ],
            tips=[
                "Выслушайте клиента полностью",
                "Подтвердите, что вы понимаете его позицию",
                "Задавайте уточняющие вопросы"
            ]
        ))

    except Exception as e:
        logger.error(f"Error getting objection handling: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============ Helper Functions ============

def _format_conversation(messages: List[Dict]) -> List[ConversationMessage]:
    """Format conversation messages for widget"""
    result = []
    for msg in messages[-10:]:  # Last 10 messages
        result.append(ConversationMessage(
            text=msg.get("text", ""),
            direction="incoming" if msg.get("from_client") else "outgoing",
            time=msg.get("timestamp", "")
        ))
    return result


def _calculate_temperature(lead_data: Dict) -> str:
    """Calculate client temperature based on engagement"""
    interactions = lead_data.get("interactions", 0)
    last_response_days = lead_data.get("days_since_response", 999)

    if interactions > 5 and last_response_days < 2:
        return "hot"
    elif interactions > 2 and last_response_days < 7:
        return "warm"
    return "cold"


async def _generate_suggestions(lead_data: Dict) -> List[Message]:
    """Generate AI message suggestions based on lead context"""
    # TODO: Integrate with LLM service
    return [
        Message(
            text="Здравствуйте! Как дела с принятием решения?",
            type="follow_up",
            channel="whatsapp"
        )
    ]


async def _get_next_action(lead_data: Dict) -> str:
    """Get recommended next action for lead"""
    stage = lead_data.get("stage", "new")
    actions = {
        "new": "Провести квалификационный звонок",
        "qualification": "Отправить коммерческое предложение",
        "proposal": "Уточнить обратную связь по КП",
        "negotiation": "Закрыть возражения и согласовать условия",
        "decision": "Подготовить договор"
    }
    return actions.get(stage, "Связаться с клиентом")


async def _get_talking_points(lead_data: Dict) -> List[str]:
    """Get AI-generated talking points"""
    # TODO: Integrate with LLM service
    return [
        "Уточнить текущие потребности",
        "Предложить решение под задачу клиента"
    ]
