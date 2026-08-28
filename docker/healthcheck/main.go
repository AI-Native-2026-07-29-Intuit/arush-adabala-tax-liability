// Static HEALTHCHECK probe for the distroless runtime image, which ships no
// shell, curl, or wget. GETs the actuator readiness endpoint and exits 0 on
// HTTP 200, non-zero otherwise - exactly what `docker inspect
// .State.Health.Status` needs to compute healthy/unhealthy. Built by a
// dedicated Docker stage (see ../../Dockerfile), never invoked outside a
// container HEALTHCHECK.
package main

import (
	"fmt"
	"net/http"
	"os"
	"time"
)

func main() {
	client := http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://localhost:8080/actuator/health/readiness")
	if err != nil {
		fmt.Fprintln(os.Stderr, "healthcheck: request failed:", err)
		os.Exit(1)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintln(os.Stderr, "healthcheck: unexpected status:", resp.StatusCode)
		os.Exit(1)
	}
	os.Exit(0)
}
