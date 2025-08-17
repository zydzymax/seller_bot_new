package main

import (
    "context"
    "fmt"
    "log"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"

    "ai_seller/config"
    "ai_seller/storage"
    "ai_seller/handlers"

    "github.com/redis/go-redis/v9"
)

func initializeDependencies() (*config.Config, *redis.Client, error) {
    cfg := config.LoadConfig()
    fmt.Printf("🔧 Конфигурация загружена: %+v\n", cfg)

    db := storage.ConnectPostgres(cfg.PostgresDSN)
    rdb := storage.ConnectRedis(cfg.RedisAddr)
    
    // Verify connections
    if db == nil {
        return nil, nil, fmt.Errorf("failed to connect to PostgreSQL")
    }
    if rdb == nil {
        return nil, nil, fmt.Errorf("failed to connect to Redis")
    }
    
    return cfg, rdb, nil
}

func setupRoutes() http.Handler {
    mux := http.NewServeMux()

    // Telegram webhook endpoint
    mux.HandleFunc("/telegram", handlers.TelegramHandler)

    // Тестовый health check
    mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
        w.Write([]byte("✅ AI-продавец трикотажа запущен"))
    })

    return mux
}

func main() {
    fmt.Println("🚀 Запуск AI-продавца...")

    cfg, _, err := initializeDependencies()
    if err != nil {
        log.Fatalf("❌ Ошибка инициализации: %v", err)
    }

    srv := &http.Server{
        Addr:    ":" + cfg.Port,
        Handler: setupRoutes(),
    }

    stop := make(chan os.Signal, 1)
    signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

    go func() {
        fmt.Printf("🌐 Сервер запущен: http://localhost:%s\n", cfg.Port)
        if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
            log.Fatalf("❌ Ошибка сервера: %v", err)
        }
    }()

    <-stop
    fmt.Println("\n⏳ Завершение работы...")

    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()

    if err := srv.Shutdown(ctx); err != nil {
        log.Fatalf("❌ Ошибка завершения: %v", err)
    }

    fmt.Println("✅ Завершение успешно.")
}

