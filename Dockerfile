FROM ubuntu:22.04

RUN apt-get update -y \
    && apt-get upgrade -y \
    && apt-get -y install build-essential \
        wget \
        git \
        curl \
        python3 \
        python3-pip

WORKDIR /usr/local/helical

COPY requirements.txt /usr/local/helical
RUN pip install -r requirements.txt
RUN pip install git+https://github.com/helicalAI/helical.git
COPY examples /usr/local/helical/examples
COPY entrypoint.sh /usr/local/helical

# Make the shell script executable
RUN chmod +x entrypoint.sh

# Define the entry point for the container
ENTRYPOINT ["/usr/local/helical/entrypoint.sh"]