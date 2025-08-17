package handlers

import (
    "encoding/json"
    "fmt"
    "log"
    "net/http"
)

// TelegramUpdate — структура запроса от Telegram с поддержкой голосовых сообщений
type TelegramUpdate struct {
    Message struct {
        Text  string `json:"text"`
        Voice *Voice `json:"voice,omitempty"`
        Audio *Audio `json:"audio,omitempty"`
        Chat  struct {
            ID int64 `json:"id"`
        } `json:"chat"`
        From struct {
            ID int64 `json:"id"`
        } `json:"from"`
    } `json:"message"`
}

type Voice struct {
    FileID       string `json:"file_id"`
    FileUniqueID string `json:"file_unique_id"`
    Duration     int    `json:"duration"`
    MimeType     string `json:"mime_type,omitempty"`
    FileSize     int    `json:"file_size,omitempty"`
}

type Audio struct {
    FileID       string `json:"file_id"`
    FileUniqueID string `json:"file_unique_id"`
    Duration     int    `json:"duration"`
    Performer    string `json:"performer,omitempty"`
    Title        string `json:"title,omitempty"`
    FileName     string `json:"file_name,omitempty"`
    MimeType     string `json:"mime_type,omitempty"`
    FileSize     int    `json:"file_size,omitempty"`
    Thumbnail    *Photo `json:"thumbnail,omitempty"`
}

type Photo struct {
    FileID       string `json:"file_id"`
    FileUniqueID string `json:"file_unique_id"`
    Width        int    `json:"width"`
    Height       int    `json:"height"`
    FileSize     int    `json:"file_size,omitempty"`
}

// TelegramHandler — базовый HTTP-хендлер для Telegram webhook
func TelegramHandler(w http.ResponseWriter, r *http.Request) {
    if r.Method != http.MethodPost {
        w.WriteHeader(http.StatusMethodNotAllowed)
        w.Write([]byte("Метод не поддерживается"))
        return
    }

    var update TelegramUpdate
    if err := json.NewDecoder(r.Body).Decode(&update); err != nil {
        log.Printf("❌ Ошибка разбора запроса Telegram: %v", err)
        w.WriteHeader(http.StatusBadRequest)
        return
    }

    if update.Message.Text != "" {
        log.Printf("📩 Текстовое сообщение от пользователя %d: %s", 
            update.Message.From.ID, update.Message.Text)
        fmt.Fprintf(w, "Принято текстовое сообщение: %s", update.Message.Text)
    } else if update.Message.Voice != nil {
        log.Printf("🎤 Голосовое сообщение от пользователя %d: файл %s (длительность: %d сек)", 
            update.Message.From.ID, update.Message.Voice.FileID, update.Message.Voice.Duration)
        fmt.Fprintf(w, "Принято голосовое сообщение: %s", update.Message.Voice.FileID)
    } else if update.Message.Audio != nil {
        log.Printf("🎵 Аудио сообщение от пользователя %d: файл %s", 
            update.Message.From.ID, update.Message.Audio.FileID)
        fmt.Fprintf(w, "Принято аудио сообщение: %s", update.Message.Audio.FileID)
    } else {
        log.Printf("❓ Неподдерживаемый тип сообщения от пользователя %d", 
            update.Message.From.ID)
        fmt.Fprintf(w, "Неподдерживаемый тип сообщения")
    }
}

