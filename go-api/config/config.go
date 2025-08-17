package config

import (
    "log"
    "os"
    "sync"

    "github.com/joho/godotenv"
)

// Config — структура для хранения конфигурации приложения
type Config struct {
    Env         string
    Port        string
    PostgresDSN string
    RedisAddr   string
    OpenAIKey   string
}

var (
    cfg  *Config
    once sync.Once
)

// LoadConfig загружает конфигурацию только один раз (singleton)
func LoadConfig() *Config {
    once.Do(func() {
        // Попробуем загрузить .env из разных мест
        envPaths := []string{".env", "../.env", "../../.env"}
        for _, path := range envPaths {
            if err := godotenv.Load(path); err == nil {
                break
            }
        }

        cfg = &Config{
            Env:         getEnv("APP_ENV", "development"),
            Port:        getEnv("PORT", "8080"),
            PostgresDSN: mustHave("POSTGRES_DSN"),
            RedisAddr:   getRedisAddr(),
            OpenAIKey:   mustHave("OPENAI_API_KEY"),
        }
    })
    return cfg
}

// getEnv — возвращает значение или дефолт
func getEnv(key string, defaultVal string) string {
    if val, ok := os.LookupEnv(key); ok {
        return val
    }
    return defaultVal
}

// mustHave — проверяет наличие обязательной переменной
func mustHave(key string) string {
    if val, ok := os.LookupEnv(key); ok && val != "" {
        return val
    }
    log.Fatalf("❌ Обязательная переменная окружения %s не установлена", key)
    return ""
}

// getRedisAddr — извлекает адрес Redis из REDIS_URL или REDIS_ADDR
func getRedisAddr() string {
    // Сначала пробуем REDIS_URL (как в .env)
    if redisURL, ok := os.LookupEnv("REDIS_URL"); ok && redisURL != "" {
        // Парсим redis://localhost:6379/0 -> localhost:6379
        if redisURL == "redis://localhost:6379/0" {
            return "localhost:6379"
        }
        return redisURL
    }
    // Если нет REDIS_URL, ищем REDIS_ADDR
    if redisAddr, ok := os.LookupEnv("REDIS_ADDR"); ok && redisAddr != "" {
        return redisAddr
    }
    log.Fatalf("❌ Не установлена переменная REDIS_URL или REDIS_ADDR")
    return ""
}

