from app.infrastructure.docker_client import DockerClient


class ServerService:
    def __init__(self):
        self.docker = DockerClient()

    def get_server_status(self, server_name):
        return self.docker.get_status(server_name)