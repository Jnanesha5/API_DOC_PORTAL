from fastapi import FastAPI, Depends
from auth import require_auth

app = FastAPI()


@app.get("/api/v1/books")
def list_books():
    """Returns a paginated list of books."""
    ...


@app.post("/api/v1/books", dependencies=[Depends(require_auth)])
def create_book():
    """Creates a new book entry."""
    ...


@app.get("/api/v1/books/{book_id}")
def get_book(book_id: str):
    """Fetch a single book by id."""
    ...


@app.delete("/api/v1/books/{book_id}", dependencies=[Depends(require_auth)])
def delete_book(book_id: str):
    """Deletes a book by id."""
    ...
