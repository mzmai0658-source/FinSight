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
        SecurityContextHolder.clearContext();
        assertThrows(BizException.class, () -> subject.download(ID, new MockHttpServletResponse()));
    }
    @Test void permitsAnOwnedPersistedReference() {
        when(mapper.findAssetMetadata(7, ID)).thenReturn(List.of("{\"references\":[{\"asset_id\":\"" + ID + "\"}]}"));
        assertDoesNotThrow(() -> controller.requireAccess(7, ID));
        verify(mapper).findAssetMetadata(7, ID);
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
}
