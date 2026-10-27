package com.finsight.chat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.mapper.ChatMessageMapper;
import com.finsight.common.exception.BizException;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import java.util.List;
import com.finsight.auth.LoginUser;
import org.junit.jupiter.api.AfterEach;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.core.io.buffer.DefaultDataBufferFactory;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AssetControllerTest {
    @AfterEach void clearSecurity() { SecurityContextHolder.clearContext(); }
    private static final String ID = "0123456789abcdef";
    private final ChatMessageMapper mapper = mock(ChatMessageMapper.class);
    private final ObjectMapper json = new ObjectMapper();
    private final AssetController controller = new AssetController(mapper, json, WebClient.builder().build());

    @Test void deniesPathsWithoutConsultingDatabase() {
        assertThrows(BizException.class, () -> controller.requireAccess(7, "../secret"));
        verifyNoInteractions(mapper);
    }
    @Test void transfersAnOwnedPdfAndKeepsAccessChecks() throws Exception {
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
            new LoginUser(7, "reader", "USER", "test"), null, List.of()));
        when(mapper.findAssetMetadata(7, ID)).thenReturn(List.of("{\"references\":[{\"asset_id\":\"" + ID + "\"}]}"));
        var client = WebClient.builder().exchangeFunction(r -> Mono.just(ClientResponse.create(HttpStatus.OK)
            .header("Content-Type", "application/pdf").header("Content-Length", "8").body("%PDFtest").build())).build();
        var subject = new AssetController(mapper, json, client);
        var response = new MockHttpServletResponse(); subject.download(ID, response);
        assertEquals("%PDFtest", response.getContentAsString());
        assertEquals("8", response.getHeader("Content-Length"));
        assertEquals("default-src 'none'; sandbox", response.getHeader("Content-Security-Policy"));
        assertNull(response.getHeader("Content-Disposition"), "Authenticated blob requests must not carry a download disposition");
        SecurityContextHolder.clearContext();
        assertThrows(BizException.class, () -> subject.download(ID, new MockHttpServletResponse()));
    }
    @Test void permitsAnOwnedPersistedReference() {
        when(mapper.findAssetMetadata(7, ID)).thenReturn(List.of("{\"references\":[{\"asset_id\":\"" + ID + "\"}]}"));
        assertDoesNotThrow(() -> controller.requireAccess(7, ID));
        verify(mapper).findAssetMetadata(7, ID);
    }
    @Test void transfersAnOwnedOriginalLargerThanTheNormalWebClientBuffer() throws Exception {
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
            new LoginUser(7, "reader", "USER", "test"), null, List.of()));
        when(mapper.findAssetMetadata(7, ID)).thenReturn(List.of("{\"facts\":[{\"source\":{\"asset_id\":\"" + ID + "\"}}]}"));
        byte[] pdf = new byte[10 * 1024 * 1024];
        byte[] signature = "%PDF-1.7\n".getBytes(java.nio.charset.StandardCharsets.US_ASCII);
        System.arraycopy(signature, 0, pdf, 0, signature.length);
        pdf[pdf.length - 1] = 'X';
        var client = WebClient.builder().codecs(config -> config.defaultCodecs().maxInMemorySize(8 * 1024 * 1024))
            .exchangeFunction(r -> Mono.just(ClientResponse.create(HttpStatus.OK)
                .header("Content-Type", "application/pdf").header("Content-Length", String.valueOf(pdf.length))
                .body(Flux.just(new DefaultDataBufferFactory().wrap(pdf))).build())).build();
        var response = new MockHttpServletResponse();
        new AssetController(mapper, json, client).download(ID, response);
        assertArrayEquals(pdf, response.getContentAsByteArray());
        assertEquals(String.valueOf(pdf.length), response.getHeader("Content-Length"));
        assertEquals("application/pdf", response.getContentType());
        assertEquals("private, no-store", response.getHeader("Cache-Control"));
        assertEquals("nosniff", response.getHeader("X-Content-Type-Options"));
        assertEquals("default-src 'none'; sandbox", response.getHeader("Content-Security-Policy"));
        assertNull(response.getHeader("Content-Disposition"));
    }
    @Test void deniesAnAssetReferencedOnlyByAnotherUser() {
        when(mapper.findAssetMetadata(8, ID)).thenReturn(List.of());
        assertThrows(BizException.class, () -> controller.requireAccess(8, ID));
    }
    @Test void sqlRowsAndFreeTextCannotGrantAssetAccess() {
        when(mapper.findAssetMetadata(7, ID)).thenReturn(List.of("{\"sql\":\"" + ID + "\",\"evidence\":[{\"type\":\"sql\",\"rows\":[{\"asset_id\":\"" + ID + "\"}]}]}"));
        assertThrows(BizException.class, () -> controller.requireAccess(7, ID));
    }
    @Test void legacyValidationAndRegisteredAssetEvidenceRemainReadable() throws Exception {
        assertTrue(AssetController.containsReference(json.readTree("{\"validation\":{\"evidence\":[{\"type\":\"asset\",\"asset_id\":\"" + ID + "\"}]}}"), ID));
        assertFalse(AssetController.containsReference(json.readTree("{\"content\":\"" + ID + "\"}"), ID));
    }
    @Test void structuredFactSourcesGrantAccessWithoutTrustingArbitraryRows() throws Exception {
        var metadata = json.readTree("{\"evidence\":[{\"type\":\"sql\",\"facts\":[{\"field\":\"revenue\",\"source\":{\"asset_id\":\"" + ID + "\"}}]}]}");
        assertTrue(AssetController.containsReference(metadata, ID));
    }
    @Test void ownedLegacyFactUrlsUseTheSameRegisteredIdentityAsTheFrontend() {
        for (String field : List.of("source_url", "asset_url")) {
            for (String suffix : List.of("", "#page=8")) {
                when(mapper.findAssetMetadata(7, ID)).thenReturn(List.of(
                    "{\"facts\":[{\"source\":{\"" + field + "\":\"/api/assets/" + ID + suffix + "\"}}]}"));
                assertDoesNotThrow(() -> controller.requireAccess(7, ID));
            }
        }
        when(mapper.findAssetMetadata(8, ID)).thenReturn(List.of());
        assertThrows(BizException.class, () -> controller.requireAccess(8, ID));
    }
    @Test void legacyFactUrlsCannotAuthorizeOtherPathsTextOrAConflictingVersion() throws Exception {
        for (String url : List.of("https://example.org/api/assets/" + ID, "/api/assets/" + ID + "?download=1",
                "/api/assets/" + ID + "/extra", "/other/" + ID)) {
            assertFalse(AssetController.containsReference(json.readTree(
                "{\"facts\":[{\"source\":{\"source_url\":\"" + url + "\"}}]}"), ID));
        }
        assertFalse(AssetController.containsReference(json.readTree(
            "{\"content\":\"/api/assets/" + ID + "\",\"evidence\":[{\"type\":\"sql\",\"rows\":[{\"source_url\":\"/api/assets/" + ID + "\"}]}]}"), ID));
        assertFalse(AssetController.containsReference(json.readTree(
            "{\"facts\":[{\"source\":{\"asset_id\":\"different_version_1234\",\"source_url\":\"/api/assets/" + ID + "\"}}]}"), ID));
    }
}
