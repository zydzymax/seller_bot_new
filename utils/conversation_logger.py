"""
Conversation Logger with Lead Attributes
Логирование диалогов с сохранением признаков лида для обучения
"""

import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum
import sqlite3
from pathlib import Path


class ConversationOutcome(str, Enum):
    """Результат диалога"""

    IN_PROGRESS = "in_progress"
    SUCCESS = "success"  # Лид закрыт, контакты получены
    FAIL_DISQUALIFIED = "fail_disqualified"  # Не подошел по критериям
    FAIL_NO_RESPONSE = "fail_no_response"  # Клиент перестал отвечать
    FAIL_OBJECTION = "fail_objection"  # Не закрыли возражение
    FAIL_NO_BUDGET = "fail_no_budget"  # Нет бюджета
    FAIL_OTHER = "fail_other"  # Другая причина


@dataclass
class LeadAttributes:
    """Признаки лида для анализа и обучения"""

    # Основные атрибуты
    business_niche: Optional[str] = None
    lead_volume: Optional[int] = None
    lead_channels: Optional[str] = None
    pain_point: Optional[str] = None
    current_solution: Optional[str] = None
    decision_authority: Optional[str] = None
    budget_range: Optional[str] = None

    # Сегментация
    client_segment: Optional[str] = None  # fast_decision, thinker, price_sensitive
    source_channel: str = "telegram"  # telegram, whatsapp, web

    # Поведенческие признаки
    avg_response_time_sec: Optional[float] = None
    avg_message_length: Optional[int] = None
    question_count: int = 0
    objection_count: int = 0

    # Sentiment
    initial_sentiment: Optional[float] = None
    final_sentiment: Optional[float] = None
    sentiment_trajectory: List[float] = field(default_factory=list)

    # Путь по воронке
    reached_stage: Optional[str] = None  # QUALIFY, DIAGNOSE, DEMO, OFFER, CLOSE
    dropped_at_stage: Optional[str] = None

    # Результат
    outcome: ConversationOutcome = ConversationOutcome.IN_PROGRESS
    outcome_reason: Optional[str] = None  # Детальная причина провала

    # Эксперимент (если есть A/B тест)
    experiment_id: Optional[str] = None
    variant: Optional[str] = None  # A или B

    # Ценность
    deal_value: Optional[float] = None
    estimated_ltv: Optional[float] = None


@dataclass
class ConversationLog:
    """Полный лог диалога"""

    conversation_id: str
    chat_id: int
    user_id: int

    # Domain
    domain: str  # textile_manufacturing, ai_seller_self

    # Lifecycle
    started_at: float
    last_message_at: Optional[float] = None
    completed_at: Optional[float] = None

    # Messages
    messages: List[Dict[str, Any]] = field(default_factory=list)
    message_count: int = 0

    # Lead attributes
    lead_attrs: LeadAttributes = field(default_factory=LeadAttributes)

    # States
    state_history: List[Dict[str, Any]] = field(default_factory=list)
    current_state: Optional[str] = None

    # Slots (collected data)
    collected_slots: Dict[str, Any] = field(default_factory=dict)

    # Events
    events: List[Dict[str, Any]] = field(default_factory=list)

    # Outcome
    outcome: ConversationOutcome = ConversationOutcome.IN_PROGRESS
    outcome_feedback: Optional[Dict[str, Any]] = None


class ConversationDB:
    """Database for conversation logs"""

    def __init__(self, db_path: str = "conversations.db"):
        self.db_path = Path(db_path)
        self.conn = None
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # Conversations table
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER,
                domain TEXT NOT NULL,

                started_at REAL NOT NULL,
                last_message_at REAL,
                completed_at REAL,

                message_count INTEGER DEFAULT 0,
                current_state TEXT,

                -- Lead attributes (JSON)
                lead_attrs TEXT,

                -- Outcome
                outcome TEXT DEFAULT 'in_progress',
                outcome_reason TEXT,
                outcome_feedback TEXT,

                -- Timestamps
                created_at REAL DEFAULT (julianday('now')),
                updated_at REAL DEFAULT (julianday('now'))
            )
        """
        )

        # Messages table
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,

                role TEXT NOT NULL,
                content TEXT NOT NULL,

                -- Metadata
                message_index INTEGER,
                timestamp REAL NOT NULL,

                -- Analysis
                sentiment REAL,
                intent TEXT,

                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            )
        """
        )

        # States history
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,

                from_state TEXT,
                to_state TEXT NOT NULL,

                trigger TEXT,
                timestamp REAL NOT NULL,

                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            )
        """
        )

        # Events
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,

                event_type TEXT NOT NULL,
                event_data TEXT,

                timestamp REAL NOT NULL,

                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            )
        """
        )

        # Outcome feedback (from /result command)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outcome_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,

                outcome TEXT NOT NULL,
                reason TEXT,

                feedback_by TEXT,
                feedback_at REAL NOT NULL,

                notes TEXT,

                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            )
        """
        )

        # Create indexes
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conv_domain
            ON conversations(domain)
        """
        )

        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conv_outcome
            ON conversations(outcome)
        """
        )

        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_msg_conv
            ON messages(conversation_id)
        """
        )

        self.conn.commit()

    def save_conversation(self, conv: ConversationLog):
        """Save or update conversation"""
        lead_attrs_json = json.dumps(asdict(conv.lead_attrs))

        self.conn.execute(
            """
            INSERT OR REPLACE INTO conversations (
                conversation_id, chat_id, user_id, domain,
                started_at, last_message_at, completed_at,
                message_count, current_state,
                lead_attrs, outcome, outcome_reason,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, julianday('now'))
        """,
            (
                conv.conversation_id,
                conv.chat_id,
                conv.user_id,
                conv.domain,
                conv.started_at,
                conv.last_message_at,
                conv.completed_at,
                conv.message_count,
                conv.current_state,
                lead_attrs_json,
                conv.outcome.value,
                conv.lead_attrs.outcome_reason,
            ),
        )

        self.conn.commit()

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        sentiment: Optional[float] = None,
        intent: Optional[str] = None,
    ):
        """Save message"""
        # Get current message index
        cursor = self.conn.execute(
            """
            SELECT MAX(message_index) as max_idx
            FROM messages
            WHERE conversation_id = ?
        """,
            (conversation_id,),
        )

        row = cursor.fetchone()
        next_idx = (row["max_idx"] or 0) + 1

        self.conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, message_index,
                timestamp, sentiment, intent
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (conversation_id, role, content, next_idx, time.time(), sentiment, intent),
        )

        self.conn.commit()

    def save_state_transition(
        self,
        conversation_id: str,
        from_state: Optional[str],
        to_state: str,
        trigger: Optional[str] = None,
    ):
        """Save state transition"""
        self.conn.execute(
            """
            INSERT INTO state_transitions (
                conversation_id, from_state, to_state, trigger, timestamp
            ) VALUES (?, ?, ?, ?, ?)
        """,
            (conversation_id, from_state, to_state, trigger, time.time()),
        )

        self.conn.commit()

    def save_event(
        self, conversation_id: str, event_type: str, event_data: Optional[Dict] = None
    ):
        """Save event"""
        event_data_json = json.dumps(event_data) if event_data else None

        self.conn.execute(
            """
            INSERT INTO conversation_events (
                conversation_id, event_type, event_data, timestamp
            ) VALUES (?, ?, ?, ?)
        """,
            (conversation_id, event_type, event_data_json, time.time()),
        )

        self.conn.commit()

    def get_last_selected_variant(self, conversation_id: str) -> Optional[Dict[str, str]]:
        """
        Получить последний выбранный вариантом обучения ответ для диалога.

        Возвращает:
            {"variant_id": "...", "variant_context": "..."} или None
        """
        cursor = self.conn.execute(
            """
            SELECT event_data
            FROM conversation_events
            WHERE conversation_id = ?
              AND event_type = 'variant_selected'
            ORDER BY timestamp DESC
            LIMIT 1
        """,
            (conversation_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        raw_data = row["event_data"]
        if not raw_data:
            return None

        try:
            payload = json.loads(raw_data)
        except Exception:
            return None

        variant_id = payload.get("variant_id")
        variant_context = payload.get("variant_context")
        if not variant_id or not variant_context:
            return None

        return {"variant_id": str(variant_id), "variant_context": str(variant_context)}

    def save_outcome_feedback(
        self,
        conversation_id: str,
        outcome: str,
        reason: Optional[str] = None,
        feedback_by: str = "admin",
        notes: Optional[str] = None,
    ):
        """
        Save outcome feedback (from /result command)

        Args:
            conversation_id: Conversation ID
            outcome: success or fail
            reason: Reason for outcome
            feedback_by: Who provided feedback
            notes: Additional notes
        """
        self.conn.execute(
            """
            INSERT INTO outcome_feedback (
                conversation_id, outcome, reason,
                feedback_by, feedback_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
            (conversation_id, outcome, reason, feedback_by, time.time(), notes),
        )

        # Update conversation outcome
        self.conn.execute(
            """
            UPDATE conversations
            SET outcome = ?,
                outcome_reason = ?,
                updated_at = julianday('now')
            WHERE conversation_id = ?
        """,
            (outcome, reason, conversation_id),
        )

        self.conn.commit()

    def get_conversation(self, conversation_id: str) -> Optional[Dict]:
        """Get conversation by ID"""
        cursor = self.conn.execute(
            """
            SELECT * FROM conversations
            WHERE conversation_id = ?
        """,
            (conversation_id,),
        )

        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def get_successful_conversations(
        self, domain: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """Get successful conversations for learning"""
        query = """
            SELECT * FROM conversations
            WHERE outcome = 'success'
        """
        params = []

        if domain:
            query += " AND domain = ?"
            params.append(domain)

        query += " ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_failed_conversations(
        self,
        domain: Optional[str] = None,
        failure_reason: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Get failed conversations for analysis"""
        query = """
            SELECT * FROM conversations
            WHERE outcome LIKE 'fail_%'
        """
        params = []

        if domain:
            query += " AND domain = ?"
            params.append(domain)

        if failure_reason:
            query += " AND outcome_reason = ?"
            params.append(failure_reason)

        query += " ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_conversation_stats(self, domain: Optional[str] = None) -> Dict:
        """Get conversation statistics"""
        query = """
            SELECT
                outcome,
                COUNT(*) as count,
                AVG(message_count) as avg_messages
            FROM conversations
        """
        params = []

        if domain:
            query += " WHERE domain = ?"
            params.append(domain)

        query += " GROUP BY outcome"

        cursor = self.conn.execute(query, params)
        stats = {}
        for row in cursor.fetchall():
            stats[row["outcome"]] = {
                "count": row["count"],
                "avg_messages": row["avg_messages"],
            }

        return stats

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()


# Global instance
_conversation_db = None


def get_conversation_db() -> ConversationDB:
    """Get global conversation database instance"""
    global _conversation_db
    if _conversation_db is None:
        db_path = Path(__file__).parent.parent / "conversations.db"
        _conversation_db = ConversationDB(str(db_path))
    return _conversation_db


if __name__ == "__main__":
    # Test
    db = ConversationDB("test_conversations.db")

    # Create test conversation
    conv = ConversationLog(
        conversation_id="test_123",
        chat_id=12345,
        user_id=67890,
        domain="ai_seller_self",
        started_at=time.time(),
    )

    conv.lead_attrs.business_niche = "real_estate"
    conv.lead_attrs.lead_volume = 50
    conv.lead_attrs.pain_point = "slow_response"
    conv.lead_attrs.client_segment = "fast_decision"

    db.save_conversation(conv)
    db.save_message("test_123", "user", "Привет!", sentiment=0.8)
    db.save_message("test_123", "assistant", "Здравствуйте!", sentiment=0.9)

    print("Stats:", db.get_conversation_stats())

    db.close()
