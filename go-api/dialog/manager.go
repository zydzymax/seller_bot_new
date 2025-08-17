package dialog

import "fmt"

// DialogManager описывает интерфейс управления диалогом
type DialogManager interface {
    HandleMessage(userID string, input string) (string, error)
}

// DefaultManager — базовая реализация DialogManager
type DefaultManager struct{}

// NewManager — фабрика для создания менеджера
func NewManager() DialogManager {
    return &DefaultManager{}
}

// HandleMessage — обработка входящего сообщения
func (m *DefaultManager) HandleMessage(userID string, input string) 
(string, error) {
    // TODO: В будущем здесь будет вызов LLM и логика контекста
    fmt.Printf("📨 [%s] %s\n", userID, input)
    return "🔁 Ответ будет здесь (заглушка)", nil
}

