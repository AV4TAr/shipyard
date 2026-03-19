"""CLI for the shareable todo list app."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from .models import TodoStore


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="todo", description="Shareable Todo Lists")
    sub = parser.add_subparsers(dest="command")

    # -- new: create a list --
    new_p = sub.add_parser("new", help="Create a new todo list")
    new_p.add_argument("--title", default="My Todo List", help="List title")

    # -- add: add an item --
    add_p = sub.add_parser("add", help="Add an item to a list")
    add_p.add_argument("list_id", help="List ID")
    add_p.add_argument("text", help="Item text")

    # -- show: display a list --
    show_p = sub.add_parser("show", help="Show a todo list")
    show_p.add_argument("list_id", help="List ID")

    # -- done: toggle an item --
    done_p = sub.add_parser("done", help="Toggle an item done/undone")
    done_p.add_argument("list_id", help="List ID")
    done_p.add_argument("item_id", type=int, help="Item ID")

    # -- rm: delete an item --
    rm_p = sub.add_parser("rm", help="Delete an item")
    rm_p.add_argument("list_id", help="List ID")
    rm_p.add_argument("item_id", type=int, help="Item ID")

    args = parser.parse_args(argv)
    store = TodoStore()

    if args.command == "new":
        todo = store.create_list(title=args.title)
        print(f"Created list: {todo.list_id}")
        print(f"Share URL:    http://localhost:8002/lists/{todo.list_id}")

    elif args.command == "add":
        item = store.add_item(args.list_id, args.text)
        print(f"Added item #{item.id}: {item.text}")

    elif args.command == "show":
        todo = store.get_list(args.list_id)
        if not todo:
            print(f"List '{args.list_id}' not found.")
            sys.exit(1)
        print(f"\n  {todo.title}  ({todo.list_id})")
        print(f"  {'=' * 40}")
        if not todo.items:
            print("  (empty)")
        for item in todo.items:
            check = "x" if item.done else " "
            print(f"  [{check}] #{item.id}  {item.text}")
        print()

    elif args.command == "done":
        item = store.toggle_item(args.list_id, args.item_id)
        if not item:
            print("Item not found.")
            sys.exit(1)
        status = "done" if item.done else "not done"
        print(f"Item #{item.id} marked {status}")

    elif args.command == "rm":
        if store.delete_item(args.list_id, args.item_id):
            print(f"Deleted item #{args.item_id}")
        else:
            print("Item not found.")
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
