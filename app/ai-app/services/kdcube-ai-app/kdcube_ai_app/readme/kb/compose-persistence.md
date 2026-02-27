
# Setup local persistence
1. Create .env file with the following content:
```.env
POSTGRES_HOST="localhost:5432"
POSTGRES_USER=<POSTGRES_USER>
POSTGRES_PASSWORD=<POSTGRES_PASSWORD>
POSTGRES_DATABASE=kdcube
DEBUG=
```

2. Run the [docker-compose.yaml](../../../../../deployment/docker/local-infra-stack/docker-compose.yaml)

3. Follow instructions from [DB-OPS-README.md](DB-OPS-README.md) to connect to the database and setup your account.


# RDS
Using SSH tunnel with port forwarding

```bash
ssh -i ~/.ssh/id_rsa -L 5432:rds-instance.<region>.rds.amazonaws.com:5432 ubuntu@bastion -N -f
```
