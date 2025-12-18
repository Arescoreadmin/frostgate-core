from agent.app.core_client import CoreClient

def run_once():
    c = CoreClient()
    try:
        data = c.get_decisions({"page_size": 50})
        # analyze data["items"] ...
        print({"seen": len(data.get("items", []))})
    finally:
        c.close()

if __name__ == "__main__":
    run_once()
