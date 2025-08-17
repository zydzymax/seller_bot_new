package jobs

import (
    "context"
    "encoding/json"
    "fmt"

    "github.com/redis/go-redis/v9"
)

// Task — структура задачи для очереди
type Task struct {
    Type string      `json:"type"`
    Data interface{} `json:"data"`
}

// PublishTask отправляет задачу в Redis-очередь
func PublishTask(rdb *redis.Client, task Task) error {
    ctx := context.Background()

    payload, err := json.Marshal(task)
    if err != nil {
        return fmt.Errorf("ошибка сериализации задачи: %w", err)
    }

    // Отправляем в список (можно заменить на pubsub при необходимости)
    if err := rdb.LPush(ctx, "queue:tasks", payload).Err(); err != nil {
        return fmt.Errorf("ошибка отправки в очередь: %w", err)
    }

    fmt.Println("📨 Задача отправлена в Redis:", task.Type)
    return nil
}

