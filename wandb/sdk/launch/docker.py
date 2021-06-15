import logging
import os
import posixpath
import shutil
import tempfile

from dockerpycreds.utils import find_executable
import docker

import wandb
from wandb.errors import ExecutionException

from .utils import WANDB_DOCKER_WORKDIR_PATH, _is_wandb_local_uri, _is_wandb_dev_uri
from ..lib.git import GitRepo

_logger = logging.getLogger(__name__)

_GENERATED_DOCKERFILE_NAME = "Dockerfile.wandb-autogenerated"
_PROJECT_TAR_ARCHIVE_NAME = "wandb-project-docker-build-context"


def validate_docker_installation():
    """
    Verify if Docker is installed on host machine.
    """
    if not find_executable("docker"):
        raise ExecutionException(
            "Could not find Docker executable. "
            "Ensure Docker is installed as per the instructions "
            "at https://docs.docker.com/install/overview/."
        )


def validate_docker_env(project):
    if not project.name:
        raise ExecutionException(
            "Project name must be specified when using docker " "for image tagging."
        )
    if not project.docker_env.get("image"):
        raise ExecutionException(
            "Project with docker environment must specify the docker image "
            "to use via an 'image' field under the 'docker_env' field."
        )


def build_docker_image(work_dir, repository_uri, base_image, api):
    """
    Build a docker image containing the project in `work_dir`, using the base image.
    """

    image_uri = _get_docker_image_uri(repository_uri=repository_uri, work_dir=work_dir)
    if _is_wandb_local_uri(api.settings("base_url")):
        _, _, port = _, _, port = api.settings("base_url").split(":")
        base_url = "http://host.docker.internal:{}".format(port)
    elif _is_wandb_dev_uri(api.settings("base_url")):
        base_url = "http://host.docker.internal:9002"
    else:
        base_url = api.settings("base_url")
    dockerfile = (
        "FROM {imagename}\n"
        "COPY {build_context_path}/ {workdir}\n"
        "WORKDIR {workdir}\n"
        "ENV WANDB_BASE_URL={base_url}\n"  # todo this is also currently passed in via r2d
        "ENV WANDB_API_KEY={api_key}\n"  # todo this is also currently passed in via r2d
        "USER root\n"  # todo: very bad idea, just to get it working
    ).format(
        imagename=base_image,
        build_context_path=_PROJECT_TAR_ARCHIVE_NAME,
        workdir=WANDB_DOCKER_WORKDIR_PATH,
        base_url=base_url,
        api_key=api.api_key,
    )
    build_ctx_path = _create_docker_build_ctx(work_dir, dockerfile)
    with open(build_ctx_path, "rb") as docker_build_ctx:
        _logger.info("=== Building docker image %s ===", image_uri)
        #  TODO: replace with shelling out
        dockerfile = posixpath.join(
            _PROJECT_TAR_ARCHIVE_NAME, _GENERATED_DOCKERFILE_NAME
        )
        # TODO: remove the dependency on docker / potentially just do the append builder
        # found at: https://github.com/google/containerregistry/blob/master/client/v2_2/append_.py
        client = docker.from_env()
        image, _ = client.images.build(
            tag=image_uri,
            forcerm=True,
            dockerfile=dockerfile,
            fileobj=docker_build_ctx,
            custom_context=True,
            encoding="gzip",
        )
    try:
        os.remove(build_ctx_path)
    except Exception:
        _logger.info(
            "Temporary docker context file %s was not deleted.", build_ctx_path
        )
    return image


def _get_docker_image_uri(repository_uri, work_dir):
    """
    Returns an appropriate Docker image URI for a project based on the git hash of the specified
    working directory.
    :param repository_uri: The URI of the Docker repository with which to tag the image. The
                           repository URI is used as the prefix of the image URI.
    :param work_dir: Path to the working directory in which to search for a git commit hash
    """
    repository_uri = (
        repository_uri.replace(" ", "-") if repository_uri else "docker-project"
    )
    # Optionally include first 7 digits of git SHA in tag name, if available.

    git_commit = GitRepo(work_dir).last_commit
    version_string = ":" + git_commit[:7] if git_commit else ""
    return repository_uri + version_string


def _create_docker_build_ctx(work_dir, dockerfile_contents):
    """
    Creates build context tarfile containing Dockerfile and project code, returning path to tarfile
    """
    directory = tempfile.mkdtemp()
    try:
        dst_path = os.path.join(directory, "wandb-project-contents")
        shutil.copytree(src=work_dir, dst=dst_path)
        with open(os.path.join(dst_path, _GENERATED_DOCKERFILE_NAME), "w") as handle:
            handle.write(dockerfile_contents)
        _, result_path = tempfile.mkstemp()
        wandb.util.make_tarfile(
            output_filename=result_path,
            source_dir=dst_path,
            archive_name=_PROJECT_TAR_ARCHIVE_NAME,
        )
    finally:
        shutil.rmtree(directory)
    return result_path


def get_docker_tracking_cmd_and_envs(tracking_uri):
    cmds = []
    env_vars = dict()

    # TODO: maybe add our sweet env vars here?
    return cmds, env_vars
