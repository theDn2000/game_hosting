import docker
from docker.errors import NotFound


class DockerClient:
    def __init__(self):
        self.client = docker.from_env()

    def get_container(self, container_name):
        try:
            return self.client.containers.get(container_name)
        except NotFound:
            return None

    def get_status(self, container_name):
        container = self.get_container(container_name)

        if container is None:
            return "not_found"

        return container.status