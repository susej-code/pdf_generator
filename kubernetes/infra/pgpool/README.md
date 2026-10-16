# pgpool-II (prod, ns `aspe`)

El 16-oct-2026 se exportó del cluster, porque hasta entonces no estaba en ningún repo. Ese día `kubectl diff -f pgpool.yaml` no mostró diferencias.

- `pgpool.yaml`: el Deployment (3 réplicas, `nodeSelector us-dallas`) y el Service.
- `pgpool.conf` y `pool_hba.conf`: el contenido del ConfigMap `pgpool-config`.
- **`pcp.conf` no está en git**, porque tiene un hash de contraseña. Sigue solo en el ConfigMap del cluster.
- Los secrets (`pgpool-passwd`, `postgresql-prod-superuser`, certificados) tampoco están en git.

## Aplicar un cambio
```sh
kubectl diff -f pgpool.yaml          # revisar antes
kubectl apply -f pgpool.yaml         # reinicia los pods de a uno
```
Para cambiar `pgpool.conf` o `pool_hba.conf`, edita el ConfigMap en el cluster: `kubectl edit cm pgpool-config -n aspe`. **No lo regeneres desde estos archivos**, porque se perdería `pcp.conf`. Después reinicia el deployment (`kubectl rollout restart deploy/pgpool -n aspe`) y copia aquí el cambio.
