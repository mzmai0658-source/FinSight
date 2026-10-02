package com.finsight.common.validation;

import jakarta.validation.Constraint;
import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import jakarta.validation.Payload;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;
import static java.lang.annotation.ElementType.*;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

@Target({FIELD, PARAMETER, RECORD_COMPONENT, METHOD, ANNOTATION_TYPE})
@Retention(RUNTIME)
@Constraint(validatedBy = QuestionText.Validator.class)
public @interface QuestionText {
    String message() default "问题不能为空，且最多2000个字符";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};

    class Validator implements ConstraintValidator<QuestionText, String> {
        static boolean whitespace(int cp) {
            return Character.isWhitespace(cp) || Character.isSpaceChar(cp) || cp == 0x85;
        }
        public static String normalize(String value) {
            if (value == null) return null;
            int start = 0, end = value.length();
            while (start < end && whitespace(value.codePointAt(start))) start += Character.charCount(value.codePointAt(start));
            while (end > start && whitespace(value.codePointBefore(end))) end -= Character.charCount(value.codePointBefore(end));
            return value.substring(start, end);
        }
        public boolean isValid(String value, ConstraintValidatorContext context) {
            String normalized = normalize(value);
            return normalized != null && !normalized.isEmpty() && normalized.codePointCount(0, normalized.length()) <= 2000;
        }
    }
}
