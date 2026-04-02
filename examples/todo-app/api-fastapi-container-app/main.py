from __future__ import annotations

from itertools import count
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

TodoTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class Todo(BaseModel):
    id: int
    title: str
    completed: bool = False


class CreateTodoRequest(BaseModel):
    title: TodoTitle


class UpdateTodoRequest(BaseModel):
    completed: bool


class TodoStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._ids = count(1)
        self._todos: dict[int, Todo] = {}

    def list(self) -> list[Todo]:
        with self._lock:
            return [todo.model_copy(deep=True) for todo in self._todos.values()]

    def create(self, title: str) -> Todo:
        with self._lock:
            todo = Todo(id=next(self._ids), title=title, completed=False)
            self._todos[todo.id] = todo
            return todo.model_copy(deep=True)

    def update(self, todo_id: int, completed: bool) -> Todo:
        with self._lock:
            todo = self._todos.get(todo_id)
            if todo is None:
                raise KeyError(todo_id)
            todo.completed = completed
            return todo.model_copy(deep=True)


def create_app() -> FastAPI:
    app = FastAPI(title="Todo API", version="0.1.0")
    store = TodoStore()

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "todo-api"}

    @app.get("/api/todos")
    async def list_todos() -> dict[str, list[Todo]]:
        return {"items": store.list()}

    @app.post("/api/todos", status_code=201)
    async def create_todo(payload: CreateTodoRequest) -> Todo:
        return store.create(payload.title)

    @app.patch("/api/todos/{todo_id}")
    async def update_todo(todo_id: int, payload: UpdateTodoRequest) -> Todo:
        try:
            return store.update(todo_id, payload.completed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Todo not found") from exc

    return app


app = create_app()
