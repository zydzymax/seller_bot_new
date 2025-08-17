package storage

import (
    "database/sql"
    "fmt"
    "log"
    "time"

    _ "github.com/lib/pq"
)

// ConnectPostgres устанавливает соединение с PostgreSQL
func ConnectPostgres(dsn string) *sql.DB {
    db, err := sql.Open("postgres", dsn)
    if err != nil {
        log.Fatalf("❌ Ошибка подключения к PostgreSQL: %v", err)
    }

    // Настройка пула соединений
    db.SetMaxOpenConns(10)
    db.SetMaxIdleConns(5)
    db.SetConnMaxLifetime(time.Hour)

    // Проверка соединения
    if err := db.Ping(); err != nil {
        log.Fatalf("❌ PostgreSQL не отвечает: %v", err)
    }

    fmt.Println("✅ Подключение к PostgreSQL успешно")
    return db
}

