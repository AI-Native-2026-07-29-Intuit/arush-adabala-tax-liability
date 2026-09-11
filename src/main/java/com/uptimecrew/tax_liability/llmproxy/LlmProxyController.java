package com.uptimecrew.tax_liability.llmproxy;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;

import com.uptimecrew.tax_liability.llm.AnthropicChatUpstream;
import com.uptimecrew.tax_liability.llm.cost.CallContext;
import com.uptimecrew.tax_liability.llm.cost.CostMiddleware;
import com.uptimecrew.tax_liability.llm.cost.PriceBook;
import com.uptimecrew.tax_liability.llm.cost.UpstreamResponse;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import io.swagger.v3.oas.annotations.tags.Tag;

import jakarta.servlet.http.HttpServletResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The LLM proxy's completion route (W6 D4 Task 2): {@code POST /v1/completions}.
 *
 * <p><b>Why this exists alongside
 * {@link com.uptimecrew.tax_liability.api.TaxpayerController#explanation}.</b> That route is a
 * product feature that happens to spend money; this one is the provider boundary itself, exposed
 * so that any caller - another service, a k6 script, a curl - can buy a completion and be charged
 * for it through exactly the same accounting path. Both funnel through {@link CostMiddleware}, so
 * there is one place where a call becomes a cost, not two. A second cost path is how a cost model
 * ends up wrong: the newer one gets the fix, the older one keeps under-reporting, and the two only
 * disagree on the invoice.
 *
 * <p><b>It is not under {@code /api/**}.</b> The path is the OpenAI-shaped {@code /v1/completions}
 * on purpose - it is the address a proxy is expected to answer on, which is what lets a caller
 * point at this service without learning its internal URI scheme. It is still authenticated and
 * still rate limited; see {@link com.uptimecrew.tax_liability.security.SecurityConfig} and
 * {@link com.uptimecrew.tax_liability.security.RateLimitFilter}, both of which had to learn about
 * this prefix explicitly, since the filter chain's default is {@code denyAll}.
 *
 * <p><b>The model is priced before it is called.</b> {@link PriceBook#priceFor(String)} would throw
 * inside the middleware anyway, but by then the tokens are already bought and the caller gets a 500
 * for a call that succeeded upstream and cost real money nobody can attribute. Checking first turns
 * that into a 400 that costs nothing.
 */
@RestController
@RequestMapping("/v1")
@Tag(name = "LLM proxy", description = "Cost-tracked completion proxy; every response carries X-Cost-Usd")
public class LlmProxyController {

    /**
     * Same scope/role pair the taxpayer read routes use. The proxy spends money on a taxpayer's
     * behalf, so it is gated at least as tightly as reading that taxpayer's record.
     */
    private static final String READ_AUTHORITY =
            "hasAuthority('SCOPE_taxpayers.read') and hasRole('TAXPAYER_READER')";

    private static final Logger LOG = LoggerFactory.getLogger(LlmProxyController.class);

    private final AnthropicChatUpstream upstream;
    private final CostMiddleware costMiddleware;
    private final String service;

    public LlmProxyController(AnthropicChatUpstream upstream, CostMiddleware costMiddleware,
            @Value("${taxcalc.cost.service:taxcalc}") String service) {
        this.upstream = Objects.requireNonNull(upstream, "upstream must not be null");
        this.costMiddleware = Objects.requireNonNull(costMiddleware, "costMiddleware must not be null");
        this.service = Objects.requireNonNull(service, "service must not be null");
    }

    @PostMapping("/completions")
    @PreAuthorize(READ_AUTHORITY)
    @Operation(summary = "Buy one LLM completion through the cost-tracked boundary",
            description = "Calls the Anthropic API with a bare model id, emits one CloudWatch EMF cost "
                    + "line (namespace uptimecrew/llmproxy), and returns the call's cost in the "
                    + "X-Cost-Usd response header.")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Completion generated; X-Cost-Usd header set"),
        @ApiResponse(responseCode = "400", description = "Blank prompt/feature, or a model the price book cannot price"),
        @ApiResponse(responseCode = "401", description = "Missing or invalid JWT"),
        @ApiResponse(responseCode = "403", description = "JWT present but lacks required scope or role"),
        @ApiResponse(responseCode = "429", description = "Rate limit exceeded - the LLM cost control")
    })
    public ResponseEntity<CompletionResponse> completions(@RequestBody CompletionRequest request,
            @AuthenticationPrincipal Jwt jwt, HttpServletResponse response) {
        String tenant = tenantOf(jwt);
        String model = request.model();

        // Fail before spending, not after - see the class javadoc.
        PriceBook.priceFor(model);

        CallContext ctx = new CallContext(Instant.now(), service, tenant, request.feature(), response);
        UpstreamResponse resp = costMiddleware.observe(ctx, c -> upstream.complete(request.prompt(), model));

        LOG.info("completions ok tenant={} feature={} model={} resolved={} tokens.in={} tokens.out={}",
                tenant, request.feature(), resp.modelId(), resp.resolvedModelId(),
                resp.inputTokens(), resp.outputTokens());

        return ResponseEntity.ok(new CompletionResponse(resp.modelId(), resp.resolvedModelId(),
                request.feature(), resp.inputTokens(), resp.outputTokens(), resp.text()));
    }

    /**
     * Turns the validation failures this route can provoke into 400s.
     *
     * <p>{@link CompletionRequest}'s compact constructor and {@link PriceBook#priceFor(String)}
     * both throw {@link IllegalArgumentException} for a caller's mistake - a blank prompt, an
     * unpriceable model id. Without this they surface as 500s, which would say the server broke
     * when in fact it refused correctly.
     */
    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, String>> badRequest(IllegalArgumentException ex) {
        LOG.warn("completions rejected: {}", ex.getMessage());
        return ResponseEntity.badRequest().body(Map.of("error", "invalid_request", "detail", ex.getMessage()));
    }

    /**
     * Resolve the tenant to bill this call to, from the JWT's {@code tenant} claim.
     *
     * <p>Falls back to {@code shared} rather than throwing, matching
     * {@link com.uptimecrew.tax_liability.api.TaxpayerController}: a missing claim should degrade
     * the granularity of cost attribution, not fail the request. It must never return blank -
     * {@link CallContext} rejects a blank dimension value.
     */
    private static String tenantOf(Jwt jwt) {
        if (jwt == null) {
            return "shared";
        }
        String claim = jwt.getClaimAsString("tenant");
        return claim == null || claim.isBlank() ? "shared" : claim;
    }
}
