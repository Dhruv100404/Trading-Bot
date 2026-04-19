#!/bin/bash
# EC2 Setup Script for dhan-trader
# Run this ONCE on a fresh EC2 instance (Ubuntu 22.04+ recommended)
set -e

echo "=== Installing Docker ==="
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add current user to docker group
sudo usermod -aG docker $USER
echo "Docker installed. Log out and back in for group changes to take effect."
echo ""

echo "=== Setting timezone to IST ==="
sudo timedatectl set-timezone Asia/Kolkata

echo "=== Setup complete ==="
echo "Next steps:"
echo "1. Log out and back in (for docker group)"
echo "2. Clone the repo: git clone <your-repo-url> dhan-trader"
echo "3. cd dhan-trader"
echo "4. Run: bash deploy/start.sh"
