"""
mTLS (Mutual TLS) 配置模块

提供通用的mTLS证书加载和SSL上下文创建功能，供服务器端和客户端使用。
"""

import os
import ssl
from pathlib import Path
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class MTLSConfig:
    """mTLS配置类，用于管理证书和创建SSL上下文"""

    def __init__(self, cert_dir: str, aic: str, ca_cert_name: str = "ca.crt"):
        """
        初始化mTLS配置

        Args:
            cert_dir: 证书目录路径
            aic: Agent识别码，用于定位证书文件
            ca_cert_name: CA根证书文件名，默认为"ca.crt"
        """
        self.cert_dir = Path(cert_dir)
        self.aic = aic
        self.ca_cert_name = ca_cert_name

        # 构建证书文件路径
        self.cert_file = self.cert_dir / f"{aic}.crt"
        self.key_file = self.cert_dir / f"{aic}.key"
        self.ca_cert_file = self.cert_dir / ca_cert_name

        # 验证文件存在
        self._validate_files()

    def _validate_files(self):
        """验证所有必需的证书文件是否存在"""
        missing_files = []

        if not self.cert_file.exists():
            missing_files.append(str(self.cert_file))
        if not self.key_file.exists():
            missing_files.append(str(self.key_file))
        if not self.ca_cert_file.exists():
            missing_files.append(str(self.ca_cert_file))

        if missing_files:
            raise FileNotFoundError(
                f"Missing certificate files: {', '.join(missing_files)}"
            )

        logger.info(
            f"mTLS certificates validated for AIC={self.aic}: "
            f"cert={self.cert_file}, key={self.key_file}, ca={self.ca_cert_file}"
        )

    def create_server_ssl_context(self) -> ssl.SSLContext:
        """
        创建服务器端SSL上下文，用于接受mTLS连接

        Returns:
            配置好的SSL上下文对象
        """
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)

        # 加载服务器证书和私钥
        ssl_context.load_cert_chain(
            certfile=str(self.cert_file), keyfile=str(self.key_file)
        )

        # 加载CA证书用于验证客户端
        ssl_context.load_verify_locations(cafile=str(self.ca_cert_file))

        # 要求客户端提供证书（双向认证）
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        # 设置安全的协议版本
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        logger.info(f"Server SSL context created for AIC={self.aic}")
        return ssl_context

    def create_client_ssl_context(self) -> ssl.SSLContext:
        """
        创建客户端SSL上下文，用于建立mTLS连接

        Returns:
            配置好的SSL上下文对象
        """
        ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

        # 加载客户端证书和私钥
        ssl_context.load_cert_chain(
            certfile=str(self.cert_file), keyfile=str(self.key_file)
        )

        # 加载CA证书用于验证服务器
        ssl_context.load_verify_locations(cafile=str(self.ca_cert_file))

        # 要求验证服务器证书
        ssl_context.check_hostname = False  # 在本地测试环境中禁用主机名检查
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        # 设置安全的协议版本
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        logger.info(f"Client SSL context created for AIC={self.aic}")
        return ssl_context

    def get_cert_paths(self) -> Tuple[str, str, str]:
        """
        获取证书文件路径

        Returns:
            (cert_file, key_file, ca_cert_file) 的元组
        """
        return (str(self.cert_file), str(self.key_file), str(self.ca_cert_file))


def load_mtls_config_from_json(
    json_path: str, cert_dir: Optional[str] = None, ca_cert_name: str = "ca.crt"
) -> MTLSConfig:
    """
    从JSON配置文件加载mTLS配置

    Args:
        json_path: JSON配置文件路径（包含aic字段）
        cert_dir: 证书目录，如果为None则使用JSON文件同级的certs目录
        ca_cert_name: CA根证书文件名

    Returns:
        MTLSConfig对象
    """
    import json

    with open(json_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    aic = config.get("aic")
    if not aic:
        raise ValueError(f"No 'aic' field found in {json_path}")

    # 如果未指定cert_dir，使用JSON文件所在目录的上级目录下的certs目录
    if cert_dir is None:
        json_dir = Path(json_path).parent
        cert_dir = json_dir.parent / "certs"

    return MTLSConfig(cert_dir=str(cert_dir), aic=aic, ca_cert_name=ca_cert_name)
