package dashboard

import (
    "encoding/json"
    "net/http"
    "runtime"
    "time"
)

// DashboardStatus содержит краткую информацию о сервисе
type DashboardStatus struct {
    Uptime     string `json:"uptime"`
    Goroutines int    `json:"goroutines"`
    Status     string `json:"status"`
}

var startedAt = time.Now()

// Handler возвращает текущий статус сервиса
func Handler(w http.ResponseWriter, r *http.Request) {
    status := DashboardStatus{
        Uptime:     time.Since(startedAt).String(),
        Goroutines: runtime.NumGoroutine(),
        Status:     "ok",
    }

    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(status)
}

