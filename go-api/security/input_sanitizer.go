package security

import (
    "html"
    "regexp"
    "strings"
)

// SanitizeText удаляет потенциально опасные символы
func SanitizeText(input string) string {
    // Удаляем HTML-теги
    re := regexp.MustCompile(`<.*?>`)
    cleaned := re.ReplaceAllString(input, "")

    // Экранируем спецсимволы
    cleaned = html.EscapeString(cleaned)

    // Убираем лишние пробелы и управляющие символы
    cleaned = strings.TrimSpace(cleaned)

    return cleaned
}

