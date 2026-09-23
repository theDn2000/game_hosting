from app.services.server_service import ServerService


server_service = ServerService()

status = server_service.get_server_status("minecraft-dev")

print(f"Minecraft server status: {status}")