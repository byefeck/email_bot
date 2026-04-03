FROM python:3.11-slim

WORKDIR /app

# Копируем файлы requirements (если существует) и остальные файлы
COPY requirements.txt* ./
COPY main.py .
COPY state.json .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Запускаем бота
CMD ["python", "main.py"]
