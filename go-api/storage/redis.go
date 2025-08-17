package storage

import (
    "context"
    "fmt"
    "log"
    "time"

    "github.com/redis/go-redis/v9"
)

// ConnectRedis создаёт клиент Redis и проверяет соединение
func ConnectRedis(addr string) *redis.Client {
    rdb := redis.NewClient(&redis.Options{
        Addr:     addr,
        Password: "", // Без пароля по умолчанию
        DB:       0,  // БД по умолчанию
    })

    // Проверим соединение
    ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
    defer cancel()

    if err := rdb.Ping(ctx).Err(); err != nil {
        log.Fatalf("❌ Redis недоступен: %v", err)
    }

    fmt.Println("✅ Подключение к Redis успешно")
    return rdb
}

