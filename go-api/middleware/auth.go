package middleware

import (
    "net/http"
    "os"
    "strings"
)

// AuthMiddleware проверяет заголовок Authorization
func AuthMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        authHeader := r.Header.Get("Authorization")
        if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
            http.Error(w, "🚫 Нет токена авторизации", 
http.StatusUnauthorized)
            return
        }

        token := strings.TrimPrefix(authHeader, "Bearer ")
        expected := os.Getenv("API_KEY")

        if token != expected || expected == "" {
            http.Error(w, "🚫 Неверный токен", http.StatusForbidden)
            return
        }

        // Всё хорошо — пропускаем дальше
        next.ServeHTTP(w, r)
    })
}

