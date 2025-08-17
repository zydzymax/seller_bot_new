package jobs

import (
    "fmt"
    "time"
)

// StartWorker запускает фоновый воркер
func StartWorker() {
    go func() {
        for {
            // TODO: в будущем — чтение из Redis очереди
            fmt.Println("🛠 Воркер активен — жду задачи...")
            time.Sleep(10 * time.Second)
        }
    }()
}

